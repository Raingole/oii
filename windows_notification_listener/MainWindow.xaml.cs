using System.Text.Json;
using Microsoft.UI.Xaml;
using Windows.UI.Notifications.Management;
using WindowsNotificationListener.Models;
using WindowsNotificationListener.Notification;

namespace WindowsNotificationListener;

public sealed partial class MainWindow : Window
{
    private readonly NotificationListenerService _service;
    private readonly string _logPath;

    public MainWindow()
    {
        InitializeComponent();
        var filter = new NotificationFilter { AllowApps = [] };
        _service = new NotificationListenerService(filter, new DeduplicationService(), AppendDiagnostics);
        _service.NotificationReceived += OnNotificationReceived;
        _logPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "XiaozhiNotificationListener", "listener.log");
    }

    private void AppendDiagnostics(string message)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
        File.AppendAllText(_logPath, $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss}{message}{Environment.NewLine}");
        _ = DispatcherQueue.TryEnqueue(() => Output.Text += $"{message}{Environment.NewLine}");
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
        try { _service.Start(); StatusText.Text = "状态：监听中（轮询模式）"; }
        catch (Exception ex) { ShowError(ex); }
    }

    private void StopButton_Click(object sender, RoutedEventArgs e)
    {
        _service.Stop();
        StatusText.Text = "状态：已停止";
    }

    private void OnNotificationReceived(object? sender, NotificationRecord record)
    {
        var json = JsonSerializer.Serialize(record);
        _ = DispatcherQueue.TryEnqueue(() => Output.Text += $"{json}{Environment.NewLine}");
        Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
        File.AppendAllText(_logPath, $"[{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss}][WINDOWS][INFO] Notification received {json}{Environment.NewLine}");
    }

    private void ShowError(Exception ex)
    {
        var hresult = ex.HResult.ToString("X8");
        StatusText.Text = $"错误：{ex.Message} (HRESULT=0x{hresult})";
        Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
        File.AppendAllText(_logPath, $"[{DateTimeOffset.Now:O}][WINDOWS][ERROR] HRESULT=0x{hresult} {ex}{Environment.NewLine}");
    }
}
