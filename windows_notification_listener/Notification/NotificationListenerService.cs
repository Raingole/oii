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
    private readonly Action<string>? _log;
    private readonly object _stateLock = new();
    private CancellationTokenSource? _cts;
    private Task? _pollTask;

    public event EventHandler<NotificationRecord>? NotificationReceived;
    public UserNotificationListenerAccessStatus AccessStatus { get; private set; } = UserNotificationListenerAccessStatus.Unspecified;

    public NotificationListenerService(NotificationFilter filter, DeduplicationService deduplication, Action<string>? log = null)
    {
        _filter = filter;
        _deduplication = deduplication;
        _log = log;
    }

    public async Task<UserNotificationListenerAccessStatus> RequestAccessAsync()
    {
        AccessStatus = await _listener.RequestAccessAsync();
        _log?.Invoke($"[WINDOWS][INFO] RequestAccessAsync returned {AccessStatus}");
        return AccessStatus;
    }

    public void Start()
    {
        if (AccessStatus != UserNotificationListenerAccessStatus.Allowed)
            throw new InvalidOperationException("Windows notification listener permission is not Allowed.");
        Stop();
        var cts = new CancellationTokenSource();
        _cts = cts;
        _log?.Invoke("[WINDOWS][INFO] Polling started (prime read marks existing notifications as seen)");
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
        _log?.Invoke("[WINDOWS][INFO] Polling stopped");
    }

    private async Task PollAsync(CancellationToken token)
    {
        using var timer = new PeriodicTimer(PollInterval);
        try
        {
            // Prime: mark notifications that already exist as seen so the UI
            // only shows notifications arriving after Start().
            await ReadCurrentNotificationsAsync(emit: false, token);
            while (!token.IsCancellationRequested)
            {
                if (!await timer.WaitForNextTickAsync(token)) break;
                await ReadCurrentNotificationsAsync(emit: true, token);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            _log?.Invoke($"[WINDOWS][ERROR] Poll loop failed HRESULT=0x{ex.HResult:X8} {ex.Message}");
        }
    }

    private async Task ReadCurrentNotificationsAsync(bool emit, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var notifications = await _listener.GetNotificationsAsync(NotificationKinds.Toast);
        if (notifications.Count > 0)
            _log?.Invoke($"[WINDOWS][INFO] Poll fetched {notifications.Count} notification(s)");
        foreach (var notification in notifications)
        {
            try
            {
                var appName = SafeValue(() => notification.AppInfo?.DisplayInfo?.DisplayName, "Unknown app");
                var appId = SafeValue(() => notification.AppInfo?.AppUserModelId, string.Empty);
                var record = Parse(notification, appName, appId);
                if (record is null)
                {
                    _log?.Invoke($"[WINDOWS][WARN] Skipped: no toast text nodes. App={appName}");
                    continue;
                }
                if (!_filter.Accept(record))
                {
                    _log?.Invoke($"[WINDOWS][INFO] Filtered by app filter. App={appName}");
                    continue;
                }
                if (_deduplication.IsDuplicate(record))
                {
                    continue;
                }
                _log?.Invoke($"[WINDOWS][INFO] Notification accepted App={record.AppName} Title={record.Title}");
                if (emit)
                {
                    NotificationReceived?.Invoke(this, record);
                }
            }
            catch (Exception ex)
            {
                // One broken notification must never kill the polling loop.
                _log?.Invoke($"[WINDOWS][ERROR] Failed to process notification HRESULT=0x{ex.HResult:X8} {ex.Message}");
            }
        }
    }

    private static T SafeValue<T>(Func<T?> getter, T fallback)
    {
        try
        {
            var value = getter();
            return value is null ? fallback : value;
        }
        catch
        {
            return fallback;
        }
    }

    private static NotificationRecord? Parse(UserNotification notification, string appName, string appId)
    {
        var binding = notification.Notification.Visual.GetBinding("ToastGeneric");
        if (binding is null) return null;

        IReadOnlyList<AdaptiveNotificationText> elements;
        try
        {
            elements = binding.GetTextElements();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException("GetTextElements failed", ex);
        }

        var texts = elements
            .Select(x => x.Text?.Trim())
            .Where(v => !string.IsNullOrWhiteSpace(v))
            .ToList();
        if (texts.Count == 0) return null;

        var title = texts[0]!;
        var content = string.Join("\n", texts.Skip(1));
        DateTimeOffset time;
        try { time = notification.CreationTime; }
        catch { time = DateTimeOffset.UtcNow; }
        return new NotificationRecord(notification.Id.ToString(), appName, appId, title, content, time);
    }
}
