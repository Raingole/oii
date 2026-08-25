using Windows.UI.Notifications;
using Windows.UI.Notifications.Management;
using WindowsNotificationListener.Models;

namespace WindowsNotificationListener.Notification;

public sealed class NotificationListenerService
{
    // NotificationChanged event subscription is not supported for full-trust
    // desktop apps (RPC fails), so notifications are polled instead, matching
    // the proven Python listener behavior.
    private static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(1);

    private readonly UserNotificationListener _listener = UserNotificationListener.Current;
    private readonly NotificationFilter _filter;
    private readonly DeduplicationService _deduplication;
    private readonly object _stateLock = new();
    private CancellationTokenSource? _cts;
    private Task? _pollTask;

    public event EventHandler<NotificationRecord>? NotificationReceived;
    public UserNotificationListenerAccessStatus AccessStatus { get; private set; } = UserNotificationListenerAccessStatus.Unspecified;

    public NotificationListenerService(NotificationFilter filter, DeduplicationService deduplication)
    {
        _filter = filter;
        _deduplication = deduplication;
    }

    public async Task<UserNotificationListenerAccessStatus> RequestAccessAsync()
    {
        AccessStatus = await _listener.RequestAccessAsync();
        return AccessStatus;
    }

    public void Start()
    {
        if (AccessStatus != UserNotificationListenerAccessStatus.Allowed)
            throw new InvalidOperationException("Windows notification listener permission is not Allowed.");
        Stop();
        var cts = new CancellationTokenSource();
        _cts = cts;
        _pollTask = Task.Run(() => PollAsync(cts.Token));
    }

    public void Stop()
    {
        lock (_stateLock)
        {
            _cts?.Cancel();
            try { _pollTask?.Wait(TimeSpan.FromSeconds(2)); }
            catch { /* Polling already ended or is ending. */ }
            _cts?.Dispose();
            _cts = null;
            _pollTask = null;
        }
    }

    private async Task PollAsync(CancellationToken token)
    {
        using var timer = new PeriodicTimer(PollInterval);
        while (!token.IsCancellationRequested)
        {
            try
            {
                if (!await timer.WaitForNextTickAsync(token)) break;
                await ReadCurrentNotificationsAsync();
            }
            catch (OperationCanceledException) { break; }
            catch { /* Keep polling; the next tick retries the read. */ }
        }
    }

    private async Task ReadCurrentNotificationsAsync()
    {
        var notifications = await _listener.GetNotificationsAsync(NotificationKinds.Toast);
        foreach (var notification in notifications)
        {
            var record = Parse(notification);
            if (record is null || !_filter.Accept(record) || _deduplication.IsDuplicate(record)) continue;
            NotificationReceived?.Invoke(this, record);
        }
    }

    private static NotificationRecord? Parse(UserNotification notification)
    {
        var app = notification.AppInfo;
        var binding = notification.Notification.Visual.GetBinding("ToastGeneric");
        if (binding is null) return null;
        var text = binding.GetTextElements().Select(x => x.Text?.Trim()).Where(x => !string.IsNullOrWhiteSpace(x)).ToList();
        if (text.Count == 0) return null;
        var title = text[0] ?? string.Empty;
        var content = string.Join("\n", text.Skip(1));
        var appName = app.DisplayInfo.DisplayName ?? "Unknown app";
        var appId = app.AppUserModelId ?? string.Empty;
        return new NotificationRecord(
            notification.Id.ToString(), appName, appId, title, content,
            notification.CreationTime.DateTime.ToUniversalTime());
    }
}
