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
import com.xpeng.highground.p5.backend.DecisionSnapshot;
import com.xpeng.highground.p5.monitor.HighGroundMonitorService;
import com.xpeng.highground.p5.monitor.MonitorUiState;

import java.util.Locale;

public final class MainActivity extends Activity {
    private static final int NOTIFICATION_PERMISSION_REQUEST = 90;

    private EditText apiUrl;
    private EditText apiKey;
    private EditText siteId;
    private EditText vehicleId;
    private View decisionPanel;
    private View configPanel;
    private TextView monitorBadge;
    private TextView decisionLabel;
    private TextView riskBadge;
    private TextView decisionReason;
    private TextView waterValue;
    private TextView rainValue;
    private TextView receivedTime;
    private TextView monitorStatus;
    private TextView xuiStatus;
    private TextView speedValue;
    private TextView gearValue;
    private TextView weatherValue;
    private Button refreshButton;
    private Button configToggle;
    private Button stopButton;
    private HighGroundMonitorService service;
    private HighGroundMonitorService.LocalBinder serviceBinder;
    private boolean activityStarted;
    private boolean bindingRequested;
    private boolean callbackRegistered;
    private boolean monitoring;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        apiUrl = findViewById(R.id.api_url);
        apiKey = findViewById(R.id.api_key);
        siteId = findViewById(R.id.site_id);
        vehicleId = findViewById(R.id.vehicle_id);
        decisionPanel = findViewById(R.id.decision_panel);
        configPanel = findViewById(R.id.config_panel);
        monitorBadge = findViewById(R.id.monitor_badge);
        decisionLabel = findViewById(R.id.decision_label);
        riskBadge = findViewById(R.id.risk_badge);
        decisionReason = findViewById(R.id.decision_reason);
        waterValue = findViewById(R.id.water_value);
        rainValue = findViewById(R.id.rain_value);
        receivedTime = findViewById(R.id.received_time);
        monitorStatus = findViewById(R.id.monitor_status);
        xuiStatus = findViewById(R.id.xui_status);
        speedValue = findViewById(R.id.speed_value);
        gearValue = findViewById(R.id.gear_value);
        weatherValue = findViewById(R.id.weather_value);
        refreshButton = findViewById(R.id.refresh_now);
        configToggle = findViewById(R.id.config_toggle);
        stopButton = findViewById(R.id.stop_monitor);

        loadForm();
        ((Button) findViewById(R.id.start_monitor)).setOnClickListener(view -> startMonitor());
        stopButton.setOnClickListener(view -> stopMonitor());
        refreshButton.setOnClickListener(view -> {
            if (monitoring) {
                withService(HighGroundMonitorService::refreshNow);
            } else {
                startMonitor();
            }
        });
        stopButton.setEnabled(false);
        configToggle.setOnClickListener(view ->
                setConfigExpanded(configPanel.getVisibility() != View.VISIBLE));
        ((Button) findViewById(R.id.test_voice)).setOnClickListener(view ->
                withService(HighGroundMonitorService::testVoice));
        ((Button) findViewById(R.id.test_light)).setOnClickListener(view ->
                withService(HighGroundMonitorService::testLighting));
        requestNotificationPermissionIfNeeded();
    }

    @Override
    protected void onStart() {
        super.onStart();
        activityStarted = true;
        bindMonitorService();
    }

    @Override
    protected void onStop() {
        activityStarted = false;
        releaseMonitorServiceBinding();
        super.onStop();
    }

    private void bindMonitorService() {
        if (bindingRequested) {
            return;
        }
        Intent intent = new Intent(this, HighGroundMonitorService.class);
        bindingRequested = bindService(intent, connection, Context.BIND_AUTO_CREATE);
    }

    private void releaseMonitorServiceBinding() {
        clearConnectedService(true);
        if (bindingRequested) {
            unbindService(connection);
            bindingRequested = false;
        }
    }

    private void clearConnectedService(boolean unregisterCallback) {
        if (unregisterCallback && callbackRegistered && serviceBinder != null) {
            serviceBinder.unregister(uiCallback);
        }
        callbackRegistered = false;
        service = null;
        serviceBinder = null;
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
        setConfigExpanded(false);
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
        setConfigExpanded(apiUrl.getText().toString().trim().isEmpty());
    }

    private void render(MonitorUiState state) {
        monitoring = state.monitoring;
        monitorBadge.setText(state.monitoring
                ? R.string.monitor_active
                : R.string.monitor_inactive);
        monitorBadge.setBackgroundResource(state.monitoring
                ? R.drawable.bg_badge_active
                : R.drawable.bg_badge_inactive);
        refreshButton.setText(state.monitoring
                ? R.string.refresh_now
                : R.string.start_monitor);
        stopButton.setEnabled(state.monitoring);
        monitorStatus.setText(state.monitorStatus);
        xuiStatus.setText(state.xuiStatus);
        String speed = state.speedKmh == null
                ? "—"
                : String.format(Locale.US, "%.1f", state.speedKmh);
        String gear = state.rawGearCode == null ? "—" : String.valueOf(state.rawGearCode);
        speedValue.setText(getString(R.string.speed_format, speed));
        gearValue.setText(gear);
        weatherValue.setText(state.weather == null || state.weather.trim().isEmpty()
                ? "—"
                : state.weather);
        renderDecision(state.latestDecision);
    }

    private void renderDecision(DecisionSnapshot snapshot) {
        if (snapshot == null) {
            decisionPanel.setBackgroundResource(R.drawable.bg_decision_neutral);
            decisionLabel.setText(R.string.decision_waiting);
            decisionReason.setText(R.string.decision_reason_empty);
            riskBadge.setText(R.string.risk_waiting);
            riskBadge.setTextColor(getColor(R.color.highground_muted));
            riskBadge.setBackgroundResource(R.drawable.bg_risk_neutral);
            waterValue.setText(R.string.metric_water_empty);
            rainValue.setText(R.string.metric_rain_empty);
            receivedTime.setText(R.string.received_time_empty);
            return;
        }

        decisionLabel.setText(snapshot.label);
        decisionReason.setText(snapshot.reason.isEmpty()
                ? getString(R.string.decision_reason_missing)
                : snapshot.reason);
        waterValue.setText(getString(
                R.string.metric_water_format,
                formatReading(snapshot.waterLevelCm)));
        rainValue.setText(getString(
                R.string.metric_rain_format,
                formatReading(snapshot.rainfallMmH)));
        receivedTime.setText(snapshot.receivedAt.isEmpty()
                ? getString(R.string.received_event_format, snapshot.eventId)
                : getString(R.string.received_time_format, snapshot.receivedAt));
        renderRisk(snapshot.riskLevel);
    }

    private void renderRisk(String riskLevel) {
        int label;
        int panel;
        int badge;
        int color;
        switch (riskLevel) {
            case "LOW":
                label = R.string.risk_low;
                panel = R.drawable.bg_decision_low;
                badge = R.drawable.bg_risk_low;
                color = R.color.highground_low;
                break;
            case "MEDIUM":
                label = R.string.risk_medium;
                panel = R.drawable.bg_decision_medium;
                badge = R.drawable.bg_risk_medium;
                color = R.color.highground_medium;
                break;
            case "HIGH":
                label = R.string.risk_high;
                panel = R.drawable.bg_decision_high;
                badge = R.drawable.bg_risk_high;
                color = R.color.highground_high;
                break;
            case "CRITICAL":
            default:
                label = R.string.risk_critical;
                panel = R.drawable.bg_decision_critical;
                badge = R.drawable.bg_risk_critical;
                color = R.color.highground_critical;
                break;
        }
        decisionPanel.setBackgroundResource(panel);
        riskBadge.setText(label);
        riskBadge.setTextColor(getColor(color));
        riskBadge.setBackgroundResource(badge);
    }

    private static String formatReading(double value) {
        return Double.isNaN(value) ? "—" : String.format(Locale.US, "%.1f", value);
    }

    private void setConfigExpanded(boolean expanded) {
        configPanel.setVisibility(expanded ? View.VISIBLE : View.GONE);
        configToggle.setText(expanded
                ? R.string.hide_connection_settings
                : R.string.show_connection_settings);
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
            if (!activityStarted || !bindingRequested) {
                return;
            }
            serviceBinder = (HighGroundMonitorService.LocalBinder) binder;
            service = serviceBinder.service();
            serviceBinder.register(uiCallback);
            callbackRegistered = true;
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            clearConnectedService(true);
        }

        @Override
        public void onBindingDied(ComponentName name) {
            boolean shouldRebind = activityStarted;
            releaseMonitorServiceBinding();
            if (shouldRebind) {
                bindMonitorService();
            }
        }

        @Override
        public void onNullBinding(ComponentName name) {
            releaseMonitorServiceBinding();
        }
    };

    private interface ServiceAction {
        void run(HighGroundMonitorService service);
    }
}
