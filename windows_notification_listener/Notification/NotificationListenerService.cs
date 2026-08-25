using System.Xml.Linq;
using Windows.Foundation;
using Windows.UI.Notifications;
using Windows.UI.Notifications.Management;
using WindowsNotificationListener.Models;

namespace WindowsNotificationListener.Notification;

public sealed class NotificationListenerService
{
    private readonly UserNotificationListener _listener = UserNotificationListener.Current;
    private readonly NotificationFilter _filter;
    private readonly DeduplicationService _deduplication;

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

    public async Task StartAsync()
    {
        if (AccessStatus != UserNotificationListenerAccessStatus.Allowed)
            throw new InvalidOperationException("Windows notification listener permission is not Allowed.");
        _listener.NotificationChanged -= OnNotificationChanged;
        _listener.NotificationChanged += OnNotificationChanged;
        await ReadCurrentNotificationsAsync();
    }

    public void Stop() => _listener.NotificationChanged -= OnNotificationChanged;

    private async void OnNotificationChanged(UserNotificationListener sender, UserNotificationChangedEventArgs args)
    {
        try { await ReadCurrentNotificationsAsync(); }
        catch { /* The next notification will retry the read. */ }
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
