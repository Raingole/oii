using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using WindowsNotificationListener.Models;

namespace WindowsNotificationListener.Notification;

public sealed class WebSocketSenderService
{
    private static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(3);

    private readonly Uri _serverUri;
    private readonly string _deviceId;
    private readonly string _token;
    private readonly Action<string>? _log;
    private readonly Channel<NotificationRecord> _queue = Channel.CreateBounded<NotificationRecord>(100);
    private readonly object _stateLock = new();
    private CancellationTokenSource? _cts;
    private Task? _loop;

    public bool IsRegistered { get; private set; }

    public WebSocketSenderService(Uri serverUri, string deviceId, string token, Action<string>? log)
    {
        // The server authenticates the token at handshake time via the query
        // string, so it must be part of the endpoint URL (same as the Python
        // listeners), not only the register message.
        var builder = new UriBuilder(serverUri);
        var query = builder.Query?.TrimStart('?');
        builder.Query = string.IsNullOrEmpty(query)
            ? $"token={Uri.EscapeDataString(token)}"
            : $"{query}&token={Uri.EscapeDataString(token)}";
        _serverUri = builder.Uri;
        _deviceId = deviceId;
        _token = token;
        _log = log;
    }

    public void Start()
    {
        Stop();
        var cts = new CancellationTokenSource();
        _cts = cts;
        _loop = Task.Run(() => RunAsync(cts.Token));
    }

    public void Stop()
    {
        lock (_stateLock)
        {
            _cts?.Cancel();
            try { _loop?.Wait(TimeSpan.FromSeconds(2)); }
            catch { /* Loop already ended. */ }
            _cts?.Dispose();
            _cts = null;
            _loop = null;
            IsRegistered = false;
        }
        _log?.Invoke("[WS][INFO] Sender stopped");
    }

    public void Enqueue(NotificationRecord record) => _queue.Writer.TryWrite(record);

    private async Task RunAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                await RunSessionAsync(token);
            }
            catch (OperationCanceledException) when (!token.IsCancellationRequested)
            {
                IsRegistered = false;
                _log?.Invoke($"[WS][WARN] Connect timed out; retry in {RetryDelay.TotalSeconds:s}s");
                try { await Task.Delay(RetryDelay, token); }
                catch (OperationCanceledException) { break; }
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                IsRegistered = false;
                _log?.Invoke($"[WS][WARN] Connection failed HRESULT=0x{ex.HResult:X8} {ex.Message}; retry in {RetryDelay.TotalSeconds:s}s");
                try { await Task.Delay(RetryDelay, token); }
                catch (OperationCanceledException) { break; }
            }
        }
    }

    private async Task RunSessionAsync(CancellationToken token)
    {
        using var socket = new ClientWebSocket();
        // Bypass the system proxy (Clash etc.): an empty WebProxy returns no
        // proxy for every URI, forcing a direct connection like the Python
        // listeners do.
        socket.Options.Proxy = new System.Net.WebProxy();
        _log?.Invoke($"[WS][INFO] Connecting {_serverUri}");
        using var connectCts = CancellationTokenSource.CreateLinkedTokenSource(token);
        connectCts.CancelAfter(TimeSpan.FromSeconds(10));
        var connectTask = socket.ConnectAsync(_serverUri, connectCts.Token);
        var watchdog = Task.Delay(TimeSpan.FromSeconds(12), token);
        if (await Task.WhenAny(connectTask, watchdog) == watchdog)
        {
            // ConnectAsync may ignore cancellation in some hosted
            // environments; abort the socket so the loop always makes
            // progress and the failure is visible in the log.
            socket.Abort();
            throw new TimeoutException("WebSocket handshake did not complete in 12s");
        }
        await connectTask;
        _log?.Invoke($"[WS][INFO] Connected {_serverUri}");

        await SendJsonAsync(socket, new
        {
            type = "register",
            device_type = "windows",
            device_id = _deviceId,
            token = _token,
        }, token);

        var registered = await ReceiveJsonAsync(socket, token);
        if (registered is null
            || registered.Value.ValueKind != JsonValueKind.Object
            || !registered.Value.TryGetProperty("type", out var type)
            || type.GetString() != "registered")
        {
            throw new InvalidOperationException("Server did not confirm registration");
        }
        IsRegistered = true;
        _log?.Invoke($"[WS][INFO] Registered device_id={_deviceId}");

        var send = SendLoopAsync(socket, token);
        var receive = DrainReceiveAsync(socket, token);
        Task finished;
        try
        {
            finished = await Task.WhenAny(send, receive);
        }
        finally
        {
            // Unblock the other pending task so the session ends promptly.
            socket.Abort();
        }
        await finished;
    }

    private async Task SendLoopAsync(ClientWebSocket socket, CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            var record = await _queue.Reader.ReadAsync(token);
            await SendJsonAsync(socket, new
            {
                type = "notification",
                notification_id = record.NotificationId,
                device_id = _deviceId,
                app_name = record.AppName,
                title = record.Title,
                content = record.Content,
                creation_time = record.CreationTime.ToString("O"),
            }, token);
            _log?.Invoke($"[WS][INFO] Sent notification Id={record.NotificationId} App={record.AppName} Title={record.Title}");
        }
    }

    private static async Task DrainReceiveAsync(ClientWebSocket socket, CancellationToken token)
    {
        var buffer = new byte[4096];
        while (!token.IsCancellationRequested)
        {
            var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), token);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                throw new IOException("Server closed the WebSocket");
            }
        }
    }

    private static async Task SendJsonAsync(ClientWebSocket socket, object payload, CancellationToken token)
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(payload);
        await socket.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, token);
    }

    private static async Task<JsonElement?> ReceiveJsonAsync(ClientWebSocket socket, CancellationToken token)
    {
        var buffer = new byte[8192];
        var builder = new StringBuilder();
        while (!token.IsCancellationRequested)
        {
            var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), token);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                return null;
            }
            builder.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
            if (result.EndOfMessage)
            {
                try
                {
                    return JsonSerializer.Deserialize<JsonElement>(builder.ToString());
                }
                catch (JsonException)
                {
                    return null;
                }
            }
        }
        return null;
    }
}
