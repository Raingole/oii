using WindowsNotificationListener.Models;

namespace WindowsNotificationListener.Notification;

public sealed class NotificationFilter
{
    public bool Enabled { get; set; } = true;
    public List<string> AllowApps { get; set; } = [];
    public List<string> DenyApps { get; set; } = [];

    public bool Accept(NotificationRecord notification)
    {
        if (!Enabled) return false;
        if (DenyApps.Any(x => Contains(notification.AppName, x) || Contains(notification.AppId, x))) return false;
        return AllowApps.Count == 0 || AllowApps.Any(x => Contains(notification.AppName, x) || Contains(notification.AppId, x));
    }

    private static bool Contains(string value, string query) =>
        value.Contains(query, StringComparison.OrdinalIgnoreCase);
}
