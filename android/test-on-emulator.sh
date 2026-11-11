#!/usr/bin/env bash
# Runs inside the Android emulator job: installs Prometheus.apk, opens it, and saves screenshots to shots/.
set -u
APK="$1"; OUT=shots; mkdir -p "$OUT"
fail() { echo "::error::$1"; adb logcat -d | grep -iE "AndroidRuntime|FATAL|prometheus" | tail -60; exit 1; }
shot() { adb exec-out screencap -p > "$OUT/$1.png"; echo "saved $1.png"; }
crashed() { adb logcat -d | grep -q "FATAL EXCEPTION.*" && adb logcat -d | grep -A3 "FATAL EXCEPTION" | grep -q "com.reevias.prometheus"; }
tap_text() {   # tap the on-screen element whose text (or resource id) is $1
  adb shell uiautomator dump /sdcard/ui.xml >/dev/null; adb pull /sdcard/ui.xml "$OUT/ui.xml" >/dev/null
  python3 - "$1" "$OUT/ui.xml" <<'PY' > "$OUT/tap.txt"
import re, sys
want, xml = sys.argv[1], open(sys.argv[2], encoding="utf-8").read()
for m in re.finditer(r'<node [^>]*>', xml):
    n = m.group(0)
    if f'text="{want}"'.lower() in n.lower() or f'resource-id="com.reevias.prometheus:id/{want}"' in n:
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n).groups()
        x1, y1, x2, y2 = map(int, b); print((x1 + x2) // 2, (y1 + y2) // 2); break
PY
  read -r X Y < "$OUT/tap.txt" || fail "couldn't find '$1' on screen"
  adb shell input tap "$X" "$Y"
}

adb logcat -c
# like a real phone: the browser is already set up (no first-run welcome screens)
adb shell am set-debug-app --persistent com.android.chrome >/dev/null 2>&1
adb shell 'echo "_ --disable-fre --no-default-browser-check --no-first-run" > /data/local/tmp/chrome-command-line'
adb install -r "$APK" || fail "the APK didn't install"
echo "installed"
adb shell pm list packages | grep -q com.reevias.prometheus || fail "package missing after install"

# 1. first open: the address screen
adb shell am start -W -n com.reevias.prometheus/.MainActivity || fail "couldn't open the app"
sleep 4; shot 1-first-open
crashed && fail "the app crashed on first open"
adb shell uiautomator dump /sdcard/ui.xml >/dev/null; adb pull /sdcard/ui.xml "$OUT/ui.xml" >/dev/null
grep -qi 'Open Prometheus' "$OUT/ui.xml" || fail "the address screen didn't show"

# 2. a wrong address is refused
tap_text "address"
adb shell input text "http://not-safe.example"
adb shell input keyevent KEYCODE_ENTER; sleep 2; shot 2-wrong-address      # the keyboard's Go key
adb shell uiautomator dump /sdcard/ui.xml >/dev/null; adb pull /sdcard/ui.xml "$OUT/ui.xml" >/dev/null
grep -q "an https address" "$OUT/ui.xml" || fail "an http address wasn't refused"

# 3. the link from the laptop's phone page fills the address in
adb shell am force-stop com.reevias.prometheus
adb shell am start -W -a android.intent.action.VIEW -d "'prometheus://open?url=https%3A%2F%2Fexample.com'" com.reevias.prometheus \
  || fail "the open-in-app link didn't open the app"
sleep 4; shot 3-link-fills-address
adb shell uiautomator dump /sdcard/ui.xml >/dev/null; adb pull /sdcard/ui.xml "$OUT/ui.xml" >/dev/null
grep -q 'https://example.com/' "$OUT/ui.xml" || fail "the link didn't fill in the address"

# 4. open it: Chrome (or the browser) shows the page full screen (the button, with no keyboard up)
tap_text "Open Prometheus"; sleep 10; shot 4-opened
crashed && fail "the app crashed when opening the address"

# 5. reopening from the icon goes back to Prometheus (not the home screen, not the address screen)
adb shell input keyevent KEYCODE_HOME; sleep 1
adb shell monkey -p com.reevias.prometheus -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1; sleep 8; shot 5-reopened
crashed && fail "the app crashed when reopened"
top=$(adb shell dumpsys activity activities | grep -m1 -E "topResumedActivity|mResumedActivity")
echo "on top: $top"
echo "$top" | grep -qiE "launcher|nexuslauncher" && fail "opening the app again went to the home screen"
adb shell uiautomator dump /sdcard/ui.xml >/dev/null; adb pull /sdcard/ui.xml "$OUT/ui.xml" >/dev/null
if echo "$top" | grep -q "com.reevias.prometheus"; then
  grep -qiE "Opening Prometheus|open in your browser|Open Prometheus" "$OUT/ui.xml" || fail "the app showed a blank screen"
fi
echo "Android app test passed"
