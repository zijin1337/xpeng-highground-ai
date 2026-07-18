package com.xpeng.highground.p5;

import android.Manifest;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import com.xpeng.highground.p5.backend.BackendConfig;
import com.xpeng.highground.p5.monitor.HighGroundMonitorService;
import com.xpeng.highground.p5.monitor.MonitorUiState;

import java.util.Locale;

public final class MainActivity extends Activity {
    private static final int NOTIFICATION_PERMISSION_REQUEST = 90;

    private EditText apiUrl;
    private EditText apiKey;
    private EditText siteId;
    private EditText vehicleId;
    private TextView monitorStatus;
    private TextView xuiStatus;
    private TextView vehicleStatus;
    private TextView decisionStatus;
    private HighGroundMonitorService service;
    private HighGroundMonitorService.LocalBinder serviceBinder;
    private boolean bound;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        apiUrl = findViewById(R.id.api_url);
        apiKey = findViewById(R.id.api_key);
        siteId = findViewById(R.id.site_id);
        vehicleId = findViewById(R.id.vehicle_id);
        monitorStatus = findViewById(R.id.monitor_status);
        xuiStatus = findViewById(R.id.xui_status);
        vehicleStatus = findViewById(R.id.vehicle_status);
        decisionStatus = findViewById(R.id.decision_status);

        loadForm();
        ((Button) findViewById(R.id.start_monitor)).setOnClickListener(view -> startMonitor());
        ((Button) findViewById(R.id.stop_monitor)).setOnClickListener(view -> stopMonitor());
        ((Button) findViewById(R.id.refresh_now)).setOnClickListener(view ->
                withService(HighGroundMonitorService::refreshNow));
        ((Button) findViewById(R.id.test_voice)).setOnClickListener(view ->
                withService(HighGroundMonitorService::testVoice));
        ((Button) findViewById(R.id.test_light)).setOnClickListener(view ->
                withService(HighGroundMonitorService::testLighting));
        requestNotificationPermissionIfNeeded();
    }

    @Override
    protected void onStart() {
        super.onStart();
        Intent intent = new Intent(this, HighGroundMonitorService.class);
        bindService(intent, connection, Context.BIND_AUTO_CREATE);
    }

    @Override
    protected void onStop() {
        if (bound) {
            serviceBinder.unregister(uiCallback);
            unbindService(connection);
            bound = false;
            service = null;
            serviceBinder = null;
        }
        super.onStop();
    }

    private void startMonitor() {
        BackendConfig config = configFromForm();
        try {
            config.validate(BuildConfig.DEBUG);
        } catch (IllegalArgumentException error) {
            toast(error.getMessage());
            return;
        }
        SharedPreferences prefs = getSharedPreferences(
                HighGroundMonitorService.PREFS,
                MODE_PRIVATE);
        prefs.edit()
                .putString(HighGroundMonitorService.KEY_API_URL, config.apiBaseUrl)
                .putString(HighGroundMonitorService.KEY_API_KEY, config.apiKey)
                .putString(HighGroundMonitorService.KEY_SITE_ID, config.siteId)
                .putString(HighGroundMonitorService.KEY_VEHICLE_ID, config.vehicleId)
                .putBoolean(HighGroundMonitorService.KEY_MONITOR_ENABLED, true)
                .apply();

        Intent intent = new Intent(this, HighGroundMonitorService.class)
                .setAction(HighGroundMonitorService.ACTION_START);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
        toast(BuildConfig.DEBUG && config.apiBaseUrl.startsWith("http://")
                ? "监控已启动；当前是允许明文 HTTP 的调试版"
                : "监控已启动");
    }

    private void stopMonitor() {
        getSharedPreferences(HighGroundMonitorService.PREFS, MODE_PRIVATE)
                .edit()
                .putBoolean(HighGroundMonitorService.KEY_MONITOR_ENABLED, false)
                .apply();
        if (service != null) {
            service.stopMonitoring();
        } else {
            startService(new Intent(this, HighGroundMonitorService.class)
                    .setAction(HighGroundMonitorService.ACTION_STOP));
        }
    }

    private BackendConfig configFromForm() {
        return new BackendConfig(
                apiUrl.getText().toString(),
                apiKey.getText().toString(),
                siteId.getText().toString(),
                vehicleId.getText().toString());
    }

    private void loadForm() {
        SharedPreferences prefs = getSharedPreferences(
                HighGroundMonitorService.PREFS,
                MODE_PRIVATE);
        apiUrl.setText(prefs.getString(HighGroundMonitorService.KEY_API_URL, ""));
        apiKey.setText(prefs.getString(HighGroundMonitorService.KEY_API_KEY, ""));
        siteId.setText(prefs.getString(HighGroundMonitorService.KEY_SITE_ID, "garage-demo-01"));
        vehicleId.setText(prefs.getString(
                HighGroundMonitorService.KEY_VEHICLE_ID,
                "vehicle-demo-01"));
    }

    private void render(MonitorUiState state) {
        monitorStatus.setText(state.monitorStatus);
        xuiStatus.setText(state.xuiStatus);
        String speed = state.speedKmh == null
                ? "—"
                : String.format(Locale.US, "%.1f", state.speedKmh);
        String gear = state.rawGearCode == null ? "—" : String.valueOf(state.rawGearCode);
        vehicleStatus.setText(getString(
                R.string.vehicle_status_format,
                speed,
                gear,
                state.weather));
        decisionStatus.setText(state.decisionStatus);
    }

    private void withService(ServiceAction action) {
        if (service == null) {
            toast("车端服务正在连接，请稍后再试");
            return;
        }
        action.run(service);
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION_REQUEST);
        }
    }

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_LONG).show();
    }

    private final HighGroundMonitorService.UiCallback uiCallback = state ->
            runOnUiThread(() -> render(state));

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            serviceBinder = (HighGroundMonitorService.LocalBinder) binder;
            service = serviceBinder.service();
            serviceBinder.register(uiCallback);
            bound = true;
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            bound = false;
            service = null;
            serviceBinder = null;
        }
    };

    private interface ServiceAction {
        void run(HighGroundMonitorService service);
    }
}
