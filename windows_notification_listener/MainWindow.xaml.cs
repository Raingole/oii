using System.Text.Json;
using Microsoft.UI.Xaml;
using Windows.UI.Notifications.Management;
using WindowsNotificationListener.Models;
using WindowsNotificationListener.Notification;

namespace WindowsNotificationListener;

public sealed partial class MainWindow : Window
{
    private readonly NotificationListenerService _service;
    private readonly string _configPath;
    private readonly string _logPath;
    private WebSocketSenderService? _sender;

    public MainWindow()
    {
        InitializeComponent();
        var dataDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "XiaozhiNotificationListener");
        _configPath = Path.Combine(dataDir, "config.json");
        _logPath = Path.Combine(dataDir, "listener.log");
        var filter = new NotificationFilter { AllowApps = [] };
        _service = new NotificationListenerService(filter, new DeduplicationService(), AppendDiagnostics);
        _service.NotificationReceived += OnNotificationReceived;
        LoadConfig();
    }

    private async void RequestButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var status = await _service.RequestAccessAsync();
            StatusText.Text = $"状态：{status}";
            StartButton.IsEnabled = status == UserNotificationListenerAccessStatus.Allowed;
        }
        catch (Exception ex) { ShowError(ex); }
    }

    private void StartButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var serverUrl = ServerUrlBox.Text.Trim();
            var deviceId = DeviceIdBox.Text.Trim();
            var token = TokenBox.Password.Trim();
            if (string.IsNullOrWhiteSpace(serverUrl) || string.IsNullOrWhiteSpace(deviceId))
            {
                StatusText.Text = "错误：请填写服务器 WS 地址和设备 ID";
                return;
            }
            SaveConfig(serverUrl, deviceId, token);
            _sender = new WebSocketSenderService(new Uri(serverUrl), deviceId, token, AppendDiagnostics);
            _sender.Start();
            _service.Start();
            StatusText.Text = "状态：监听中（轮询模式）";
        }
        catch (Exception ex) { ShowError(ex); }
    }

    private void StopButton_Click(object sender, RoutedEventArgs e)
    {
        _service.Stop();
        _sender?.Stop();
        StatusText.Text = "状态：已停止";
    }

    private void OnNotificationReceived(object? sender, NotificationRecord record)
    {
        var json = JsonSerializer.Serialize(record);
        _ = DispatcherQueue.TryEnqueue(() => Output.Text += $"{json}{Environment.NewLine}");
        AppendDiagnostics($"[WINDOWS][INFO] Notification received {json}");
        _sender?.Enqueue(record);
    }

    private void LoadConfig()
    {
        try
        {
            var values = JsonSerializer.Deserialize<JsonElement>(File.ReadAllText(_configPath));
            ServerUrlBox.Text = values.GetProperty("server_url").GetString() ?? "ws://127.0.0.1:8003/ws/windows";
            DeviceIdBox.Text = values.GetProperty("device_id").GetString() ?? "windows-pc-001";
            TokenBox.Password = values.TryGetProperty("token", out var token) ? token.GetString() ?? string.Empty : string.Empty;
        }
        catch (Exception)
        {
            ServerUrlBox.Text = "ws://127.0.0.1:8003/ws/windows";
            DeviceIdBox.Text = "windows-pc-001";
        }
    }

    private void SaveConfig(string serverUrl, string deviceId, string token)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(_configPath)!);
        var payload = JsonSerializer.Serialize(new { server_url = serverUrl, device_id = deviceId, token });
        File.WriteAllText(_configPath, payload);
    }

    private void AppendDiagnostics(string message)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
            File.AppendAllText(_logPath, $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss}{message}{Environment.NewLine}");
        }
        catch { /* Logging must never break the app. */ }
        _ = DispatcherQueue.TryEnqueue(() => Output.Text += $"{message}{Environment.NewLine}");
    }

    private void ShowError(Exception ex)
    {
        var hresult = ex.HResult.ToString("X8");
        StatusText.Text = $"错误：{ex.Message} (HRESULT=0x{hresult})";
        AppendDiagnostics($"[WINDOWS][ERROR] HRESULT=0x{hresult} {ex}");
    }
}
