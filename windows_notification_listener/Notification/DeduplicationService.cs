using WindowsNotificationListener.Models;

namespace WindowsNotificationListener.Notification;

public sealed class DeduplicationService
{
    private readonly Dictionary<string, DateTimeOffset> _seen = [];
    private readonly object _lock = new();
    private readonly TimeSpan _retention = TimeSpan.FromMinutes(30);

    public bool IsDuplicate(NotificationRecord notification)
    {
        var key = string.IsNullOrWhiteSpace(notification.NotificationId)
            ? $"{notification.AppName}|{notification.Title}|{notification.Content}|{notification.CreationTime:O}"
            : $"{notification.AppId}|{notification.NotificationId}";
        var now = DateTimeOffset.UtcNow;
        lock (_lock)
        {
            foreach (var old in _seen.Where(x => now - x.Value > _retention).Select(x => x.Key).ToList())
                _seen.Remove(old);
            if (_seen.ContainsKey(key)) return true;
            _seen[key] = now;
            while (_seen.Count > 1000)
                _seen.Remove(_seen.OrderBy(x => x.Value).First().Key);
            return false;
        }
    }
}
