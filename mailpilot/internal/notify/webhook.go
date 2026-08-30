package notify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/Wangnov/mailpilot/internal/config"
)

type webhookNotifier struct{ cfg config.Notifier }

func (n *webhookNotifier) Name() string { return "webhook" }

func (n *webhookNotifier) Send(m Message) error {
	if n.cfg.URL == "" {
		return fmt.Errorf("webhook 缺少 url")
	}
	format := webhookFormat(n.cfg)
	content := m.Title + "\n" + m.Body
	if format == "slack" {
		code, body, err := postWebhookJSON(n.cfg, map[string]any{"text": content})
		if err != nil {
			return err
		}
		if code >= 300 || strings.TrimSpace(string(body)) != "ok" {
			return fmt.Errorf("webhook slack 返回 HTTP %d", code)
		}
		return nil
	}
	payload := map[string]any{
		"title": m.Title, "body": m.Body, "category": m.Category,
		"urgency": m.Urgency, "url": m.URL, "passive": m.Passive(),
		"sender": m.Sender, "subject": m.Subject, "summary": m.Summary,
		"key_points": m.KeyPoints, "verification_code": m.VerificationCode,
		"message_id": m.MessageID, "uid": m.UID,
		// 企业微信群机器人文本格式；generic 格式也保留这些结构化字段。
		"msgtype": "text",
		"text":    map[string]string{"content": content},
	}
	code, body, err := postWebhookJSON(n.cfg, payload)
	if err != nil {
		return err
	}
	if code >= 300 {
		return fmt.Errorf("webhook 返回 HTTP %d", code)
	}
	if format == "wecom" && len(body) > 0 {
		var r struct {
			ErrCode int    `json:"errcode"`
			ErrMsg  string `json:"errmsg"`
		}
		if json.Unmarshal(body, &r) == nil && r.ErrCode != 0 {
			return fmt.Errorf("webhook wecom errcode=%d", r.ErrCode)
		}
	}
	return nil
}

func postWebhookJSON(cfg config.Notifier, payload any) (int, []byte, error) {
	if cfg.Authorization == "" {
		return postJSON(cfg.URL, payload)
	}
	buf, _ := json.Marshal(payload)
	req, err := http.NewRequest("POST", cfg.URL, bytes.NewReader(buf))
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	req.Header.Set("Authorization", cfg.Authorization)
	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	return resp.StatusCode, b, nil
}

func webhookFormat(cfg config.Notifier) string {
	format := strings.ToLower(strings.TrimSpace(cfg.Format))
	if format != "" {
		return format
	}
	u, err := url.Parse(cfg.URL)
	if err != nil {
		return "generic"
	}
	host := strings.ToLower(u.Hostname())
	switch {
	case strings.Contains(host, "qyapi.weixin.qq.com"):
		return "wecom"
	case strings.Contains(host, "hooks.slack.com"):
		return "slack"
	default:
		return "generic"
	}
}
