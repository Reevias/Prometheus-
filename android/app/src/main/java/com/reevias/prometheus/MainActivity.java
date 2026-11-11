package com.reevias.prometheus;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import com.google.androidbrowserhelper.trusted.TwaLauncher;

/** Remembers the cloud copy's address and opens it full screen in Chrome. */
public class MainActivity extends Activity {
    private static final String KEY = "address";
    private TwaLauncher launcher;
    private boolean launched;

    /** Only https addresses: the cloud copy is always served over https by Tailscale. */
    static String clean(String text) {
        if (text == null) return null;
        String s = text.trim();
        if (s.isEmpty()) return null;
        if (!s.contains("://")) s = "https://" + s;
        Uri u = Uri.parse(s);
        if (!"https".equals(u.getScheme()) || u.getHost() == null || u.getHost().isEmpty()) return null;
        return "https://" + u.getHost() + (u.getPort() > 0 ? ":" + u.getPort() : "") + "/";
    }

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        SharedPreferences prefs = getSharedPreferences("prometheus", MODE_PRIVATE);
        Intent intent = getIntent();
        Uri data = intent.getData();
        if (data != null && "prometheus".equals(data.getScheme())) {
            String given = clean(data.getQueryParameter("url"));
            if (given != null) {
                showSetup(prefs, given);          // the owner confirms the address before it's used
                return;
            }
        }
        String address = prefs.getString(KEY, null);
        if (address == null || "com.reevias.prometheus.SETUP".equals(intent.getAction())) {
            showSetup(prefs, address);
            return;
        }
        open(address);
    }

    private void showSetup(SharedPreferences prefs, String address) {
        setContentView(R.layout.setup);
        EditText box = findViewById(R.id.address);
        TextView problem = findViewById(R.id.problem);
        Button button = findViewById(R.id.open);
        if (address != null) box.setText(address);
        Runnable go = () -> {
            String ok = clean(box.getText().toString());
            if (ok == null) {
                problem.setText("That isn't an https address. It looks like https://something.ts.net");
                return;
            }
            prefs.edit().putString(KEY, ok).apply();
            open(ok);
        };
        button.setOnClickListener(v -> go.run());
        // the keyboard's Go/✓ key opens it too (the keyboard can cover the button on small phones)
        box.setOnEditorActionListener((v, actionId, event) -> { go.run(); return true; });
    }

    private void open(String address) {
        // The browser opens Prometheus on top. Behind it this screen says what's happening and has buttons, so the
        // app is never a blank screen (a browser that's still setting up, no internet, Tailscale off…).
        setContentView(R.layout.opening);
        ((TextView) findViewById(R.id.where)).setText(address);
        findViewById(R.id.again).setOnClickListener(v -> launch(address));
        findViewById(R.id.change).setOnClickListener(v ->
                showSetup(getSharedPreferences("prometheus", MODE_PRIVATE), address));
        launch(address);
    }

    private void launch(String address) {
        if (launcher != null) launcher.destroy();
        launcher = new TwaLauncher(this);
        launcher.launch(Uri.parse(address));
        launched = true;
    }

    @Override
    protected void onRestart() {
        super.onRestart();
        if (launched) {
            TextView status = findViewById(R.id.status);
            if (status != null) status.setText("Prometheus is open in your browser.");
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (launcher != null) launcher.destroy();
    }
}
