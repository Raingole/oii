#!/system/bin/sh

# SMS Bridge KernelSU/Magisk service.d daemon. BusyBox/POSIX sh only.
BASE=/data/adb/sms-bridge
CONFIG=$BASE/config
ENABLED=$BASE/enabled
PIDFILE=$BASE/pid
STATE=$BASE/state
LAST_ID=$BASE/last_sms_id
PENDING=$BASE/pending
LOGDIR=$BASE/log
TMP=$BASE/query.tmp
SCRIPT=/data/adb/service.d/sms-bridge.sh

mkdir -p "$BASE" "$PENDING" "$LOGDIR"
chmod 700 "$BASE" "$PENDING" "$LOGDIR" 2>/dev/null

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOGDIR/daemon.log"
    if [ "$(wc -c < "$LOGDIR/daemon.log" 2>/dev/null)" -gt 65536 ]; then
        tail -c 32768 "$LOGDIR/daemon.log" > "$LOGDIR/daemon.log.tmp" && mv "$LOGDIR/daemon.log.tmp" "$LOGDIR/daemon.log"
    fi
}
state() { printf '%s\n' "$1" > "$STATE"; }
cleanup() { rm -f "$PIDFILE" "$TMP"; state STOPPED; }
trap cleanup EXIT INT TERM

if [ -f "$PIDFILE" ]; then
    OLD=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then exit 0; fi
fi
echo $$ > "$PIDFILE"
chmod 600 "$PIDFILE" "$STATE" "$LAST_ID" 2>/dev/null

while [ "$(getprop sys.boot_completed 2>/dev/null)" != "1" ]; do sleep 3; done
sleep 5

while [ ! -f "$CONFIG" ]; do
    state CONFIG_ERROR
    log "config missing: $CONFIG"
    sleep 60
done

json_escape() {
    awk 'BEGIN{ORS=""} {gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); if(NR>1) printf "\\n"; printf "%s",$0}'
}
code_from_body() {
    TEXT=$1
    printf '%s' "$TEXT" | grep -Eiq '验证码|校验码|动态码|认证码|安全码|登录码|确认码|OTP|verification[[:space:]-]+code|verify[[:space:]-]+code|security[[:space:]-]+code|one[-[:space:]]time[[:space:]-]+code|authentication[[:space:]-]+code|confirmation[[:space:]-]+code' || return 1
    for N in 6 5 4 7 8; do
        C=$(printf '%s' "$TEXT" | grep -Eo "(^|[^0-9])[0-9]{$N}([^0-9]|$)" | grep -Eo "[0-9]{$N}" | head -n 1)
        [ -n "$C" ] && { printf '%s' "$C"; return 0; }
    done
    return 1
}
json_get() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" "$2" | head -n 1; }
send_json() {
    URL=$(sed -n 's/^CONTROLLER_URL=//p' "$CONFIG" | head -n 1)
    TOKEN=$(sed -n 's/^TOKEN=//p' "$CONFIG" | head -n 1)
    [ -n "$URL" ] && [ -n "$TOKEN" ] || return 2
    if command -v curl >/dev/null 2>&1; then
        curl -sS -o "$BASE/http.response" -w '%{http_code}' --connect-timeout 5 --max-time 10 -X POST "$URL/api/events/sms" -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" --data-binary "@$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$BASE/http.response" --timeout=10 --header="Content-Type: application/json" --header="Authorization: Bearer $TOKEN" --post-file="$1" "$URL/api/events/sms" && printf '200'
    else
        return 3
    fi
}
retry_pending() {
    NOW=$(date +%s)
    for FILE in "$PENDING"/*.json; do
        [ -f "$FILE" ] || continue
        TS=$(json_get timestamp "$FILE")
        [ -n "$TS" ] || continue
        [ $((NOW * 1000 - TS)) -gt 900000 ] && { rm -f "$FILE"; continue; }
        HTTP=$(send_json "$FILE")
        case "$HTTP" in
            2*) rm -f "$FILE"; log "SMS event delivered"; state RUNNING ;;
            401|403) log "AUTH_ERROR"; state AUTH_ERROR; return 60 ;;
            *) state NETWORK_ERROR; return 5 ;;
        esac
    done
    return 0
}
baseline() {
    [ -f "$LAST_ID" ] && return 0
    content query --uri content://sms/inbox --projection _id --sort "_id DESC" --limit 1 > "$TMP" 2>/dev/null || :
    MAX=$(sed -n 's/.*_id=\([0-9][0-9]*\).*/\1/p' "$TMP" | head -n 1)
    [ -n "$MAX" ] || MAX=0
    printf '%s\n' "$MAX" > "$LAST_ID"
    log "baseline last_sms_id=$MAX"
}
poll_sms() {
    LAST=$(cat "$LAST_ID" 2>/dev/null); [ -n "$LAST" ] || LAST=0
    INTERVAL=$(sed -n 's/^POLL_INTERVAL=//p' "$CONFIG" | head -n 1)
    case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=3 ;; esac
    content query --uri content://sms/inbox --projection _id,date,address,body,sub_id --where "_id > $LAST" --sort "_id ASC" --limit 50 > "$TMP" 2>/dev/null
    if [ $? -ne 0 ]; then content query --uri content://sms/inbox --projection _id,date,address,body,sub_id --sort "_id DESC" --limit 50 > "$TMP" 2>/dev/null || return "$INTERVAL"; fi
    NEWLAST=$LAST
    while IFS= read -r ROW; do
        ID=$(printf '%s\n' "$ROW" | sed -n 's/.*_id=\([0-9][0-9]*\).*/\1/p')
        [ -n "$ID" ] && [ "$ID" -gt "$LAST" ] 2>/dev/null || continue
        DATE=$(printf '%s\n' "$ROW" | sed -n 's/.*date=\([^, ]*\).*/\1/p')
        SENDER=$(printf '%s\n' "$ROW" | sed -n 's/.*address=\(.*\), body=.*/\1/p')
        BODY=$(printf '%s\n' "$ROW" | sed 's/.*body=//; s/, sub_id=.*//')
        CODE=$(code_from_body "$BODY")
        if [ -n "$CODE" ]; then
            EVENT="sms-$ID-$DATE"; FILE="$PENDING/$EVENT.json"
            if [ ! -f "$FILE" ]; then
                B=$(printf '%s' "$BODY" | json_escape); S=$(printf '%s' "$SENDER" | json_escape)
                printf '{"event_id":"%s","timestamp":%s,"sender":"%s","body":"%s","code":"%s"}\n' "$EVENT" "$DATE" "$S" "$B" "$CODE" > "$FILE"
                chmod 600 "$FILE"; log "SMS discovered id=$ID verification=true"
            fi
        else
            log "SMS checked id=$ID verification=false"
        fi
        NEWLAST=$ID
    done < "$TMP"
    [ "$NEWLAST" -gt "$LAST" ] 2>/dev/null && printf '%s\n' "$NEWLAST" > "$LAST_ID"
    return "$INTERVAL"
}

if [ "$(cat "$ENABLED" 2>/dev/null)" != "1" ]; then
    state DISABLED
    exit 0
fi
baseline
state RUNNING
log "daemon started"
while [ "$(cat "$ENABLED" 2>/dev/null)" = "1" ]; do
    printf '%s\n' "$(date +%s)" > "$BASE/heartbeat"
    retry_pending; WAIT=$?
    [ "$WAIT" -gt 0 ] || WAIT=$(poll_sms)
    sleep "$WAIT"
done
log "daemon disabled"
