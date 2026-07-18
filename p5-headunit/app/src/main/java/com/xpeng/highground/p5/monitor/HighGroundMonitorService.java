package com.xpeng.highground.p5.monitor;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Binder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;

import com.xpeng.highground.p5.MainActivity;
import com.xpeng.highground.p5.R;
import com.xpeng.highground.p5.backend.BackendConfig;
import com.xpeng.highground.p5.backend.DecisionSnapshot;
import com.xpeng.highground.p5.backend.HighGroundBackendClient;
import com.xpeng.highground.p5.vehicle.VehicleBridge;
import com.xpeng.highground.p5.vehicle.VehicleBridgeFactory;

import java.util.Set;
import java.util.concurrent.CopyOnWriteArraySet;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

public final class HighGroundMonitorService extends Service {
    public static final String ACTION_START = "com.xpeng.highground.p5.action.START";
    public static final String ACTION_STOP = "com.xpeng.highground.p5.action.STOP";
    public static final String PREFS = "highground_p5";
    public static final String KEY_API_URL = "api_url";
    public static final String KEY_API_KEY = "api_key";
    public static final String KEY_SITE_ID = "site_id";
    public static final String KEY_VEHICLE_ID = "vehicle_id";
    public static final String KEY_MONITOR_ENABLED = "monitor_enabled";

    private static final String CHANNEL_ID = "highground_monitor";
    private static final int NOTIFICATION_ID = 7105;
    private static final long POLL_SECONDS = 15;
    private static final long VEHICLE_RECONNECT_SECONDS = 60;

    private final LocalBinder binder = new LocalBinder();
    private final Set<UiCallback> callbacks = new CopyOnWriteArraySet<>();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean pollInProgress = new AtomicBoolean();
    private final AtomicBoolean vehicleReconnectQueued = new AtomicBoolean();
    private final HighGroundBackendClient backendClient = new HighGroundBackendClient();

    private ScheduledExecutorService executor;
    private ScheduledFuture<?> pollFuture;
    private ScheduledFuture<?> vehicleReconnectFuture;
    private volatile VehicleBridge vehicleBridge;
    private volatile BackendConfig backendConfig;
    private volatile boolean monitoring;
    private volatile boolean destroyed;
    private volatile long lastVehicleConnectAttemptMs;
    private volatile String monitorStatus = "监控未启动";
    private volatile String xuiStatus = "P5 XUI：等待检测";
    private volatile Float speedKmh;
    private volatile Integer rawGearCode;
    private volatile String weather = "—";
    private volatile String decisionStatus = "服务端决策：尚无";
    private volatile String lastEventId;
    private volatile boolean warningLightWanted;

    @Override
    public void onCreate() {
        super.onCreate();
        destroyed = false;
        createNotificationChannel();
        executor = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "highground-p5-monitor");
            thread.setDaemon(true);
            return thread;
        });
        connectVehicleBridge();
        vehicleReconnectFuture = executor.scheduleWithFixedDelay(
                this::queueVehicleReconnectIfNeeded,
                VEHICLE_RECONNECT_SECONDS,
                VEHICLE_RECONNECT_SECONDS,
                TimeUnit.SECONDS);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopMonitoring();
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(action)
                || getSharedPreferences(PREFS, MODE_PRIVATE)
                .getBoolean(KEY_MONITOR_ENABLED, false)) {
            try {
                startMonitoring(loadConfig());
            } catch (IllegalArgumentException error) {
                monitorStatus = "配置无效：" + error.getMessage();
                setMonitorEnabled(false);
                publish();
                stopSelf();
            }
        }
        return monitoring ? START_STICKY : START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public void onDestroy() {
        destroyed = true;
        stopMonitoringInternal(false, false);
        if (vehicleReconnectFuture != null) {
            vehicleReconnectFuture.cancel(false);
            vehicleReconnectFuture = null;
        }
        if (vehicleBridge != null) {
            vehicleBridge.close();
            vehicleBridge = null;
        }
        if (executor != null) {
            executor.shutdownNow();
        }
        mainHandler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    public synchronized void startMonitoring(BackendConfig config) {
        config.validate(com.xpeng.highground.p5.BuildConfig.DEBUG);
        backendConfig = config;
        setMonitorEnabled(true);
        queueVehicleReconnectIfNeeded();
        if (!monitoring) {
            monitoring = true;
            lastEventId = null;
            monitorStatus = "监控已启动，每 " + POLL_SECONDS + " 秒读取一次服务端决策";
            startForeground(NOTIFICATION_ID, buildNotification(monitorStatus));
            pollFuture = executor.scheduleWithFixedDelay(
                    this::pollOnce,
                    0,
                    POLL_SECONDS,
                    TimeUnit.SECONDS);
        } else {
            monitorStatus = "配置已更新，正在刷新";
            refreshNow();
        }
        publish();
    }

    public synchronized void stopMonitoring() {
        stopMonitoringInternal(true, true);
    }

    private synchronized void stopMonitoringInternal(
            boolean stopService,
            boolean disablePersistentMonitoring) {
        monitoring = false;
        backendConfig = null;
        warningLightWanted = false;
        if (pollFuture != null) {
            pollFuture.cancel(false);
            pollFuture = null;
        }
        if (vehicleBridge != null) {
            try {
                vehicleBridge.setWarningLighting(false);
            } catch (VehicleBridge.VehicleBridgeException ignored) {
                // A missing XUI permission must not prevent the monitor from stopping.
            }
        }
        if (disablePersistentMonitoring) {
            setMonitorEnabled(false);
        }
        monitorStatus = "监控已停止";
        stopForeground(STOP_FOREGROUND_REMOVE);
        publish();
        if (stopService) {
            stopSelf();
        }
    }

    public void refreshNow() {
        if (!monitoring) {
            monitorStatus = "请先保存配置并启动监控";
            publish();
            return;
        }
        executor.execute(this::pollOnce);
    }

    public void testVoice() {
        executor.execute(() -> {
            try {
                vehicleBridge.speak(
                        AlertPolicy.VoicePriority.NORMAL,
                        "高地 AI 小鹏 P5 车端语音测试成功。本应用只提供风险提醒，不会控制车辆行驶。");
                monitorStatus = "小 P 语音测试指令已发送";
            } catch (VehicleBridge.VehicleBridgeException error) {
                monitorStatus = "小 P 语音测试失败：" + error.getMessage();
            }
            publish();
        });
    }

    public void testLighting() {
        executor.execute(() -> {
            try {
                vehicleBridge.setWarningLighting(true);
                monitorStatus = "环境灯测试已启动，5 秒后恢复";
                publish();
                executor.schedule(() -> {
                    try {
                        vehicleBridge.setWarningLighting(warningLightWanted);
                        monitorStatus = "环境灯测试结束，已恢复测试前状态";
                    } catch (VehicleBridge.VehicleBridgeException error) {
                        monitorStatus = "环境灯恢复失败：" + error.getMessage();
                    }
                    publish();
                }, 5, TimeUnit.SECONDS);
            } catch (VehicleBridge.VehicleBridgeException error) {
                monitorStatus = "环境灯测试失败：" + error.getMessage();
                publish();
            }
        });
    }

    public MonitorUiState currentState() {
        return new MonitorUiState(
                monitoring,
                monitorStatus,
                xuiStatus,
                speedKmh,
                rawGearCode,
                weather,
                decisionStatus);
    }

    private void pollOnce() {
        BackendConfig configSnapshot = backendConfig;
        if (!monitoring
                || configSnapshot == null
                || !pollInProgress.compareAndSet(false, true)) {
            return;
        }
        try {
            DecisionSnapshot snapshot = backendClient.fetchLatest(configSnapshot);
            if (!monitoring || configSnapshot != backendConfig) {
                return;
            }
            decisionStatus = "服务端决策：" + snapshot.displayText()
                    + (snapshot.receivedAt.isEmpty() ? "" : "\n接收时间：" + snapshot.receivedAt);
            monitorStatus = "后端连接正常，最近事件 " + snapshot.eventId;
            if (!snapshot.eventId.equals(lastEventId)) {
                applyNewDecision(snapshot);
                lastEventId = snapshot.eventId;
            }
            updateNotification("最近决策：" + snapshot.label + " / " + snapshot.riskLevel);
        } catch (Exception error) {
            if (monitoring && configSnapshot == backendConfig) {
                monitorStatus = "读取后端失败：" + safeMessage(error);
                updateNotification("连接异常，等待下次重试");
            }
        } finally {
            pollInProgress.set(false);
            publish();
        }
    }

    private void applyNewDecision(DecisionSnapshot snapshot) {
        AlertPolicy.AlertAction action = AlertPolicy.evaluate(snapshot);
        warningLightWanted = action.warningLight;
        try {
            vehicleBridge.setWarningLighting(action.warningLight);
        } catch (VehicleBridge.VehicleBridgeException error) {
            monitorStatus += "；灯光提醒不可用：" + error.getMessage();
        }
        if (action.voicePriority != AlertPolicy.VoicePriority.NONE) {
            try {
                vehicleBridge.speak(action.voicePriority, action.message);
            } catch (VehicleBridge.VehicleBridgeException error) {
                monitorStatus += "；语音提醒不可用：" + error.getMessage();
            }
        }
    }

    private BackendConfig loadConfig() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        return new BackendConfig(
                prefs.getString(KEY_API_URL, ""),
                prefs.getString(KEY_API_KEY, ""),
                prefs.getString(KEY_SITE_ID, ""),
                prefs.getString(KEY_VEHICLE_ID, ""));
    }

    private void setMonitorEnabled(boolean enabled) {
        getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_MONITOR_ENABLED, enabled)
                .apply();
    }

    private void connectVehicleBridge() {
        if (destroyed) {
            return;
        }
        lastVehicleConnectAttemptMs = SystemClock.elapsedRealtime();
        VehicleBridge previous = vehicleBridge;
        if (previous != null) {
            previous.close();
        }
        VehicleBridge replacement = VehicleBridgeFactory.create();
        vehicleBridge = replacement;
        replacement.connect(vehicleListener);
        if (replacement.isAvailable() && warningLightWanted && executor != null) {
            executor.execute(() -> {
                if (!destroyed && vehicleBridge == replacement && warningLightWanted) {
                    try {
                        replacement.setWarningLighting(true);
                    } catch (VehicleBridge.VehicleBridgeException error) {
                        xuiStatus = "P5 XUI：重连后恢复灯光提醒失败：" + error.getMessage();
                        publish();
                    }
                }
            });
        }
    }

    private void queueVehicleReconnectIfNeeded() {
        VehicleBridge bridge = vehicleBridge;
        long elapsedMs = SystemClock.elapsedRealtime() - lastVehicleConnectAttemptMs;
        if (destroyed
                || (bridge != null && bridge.isAvailable())
                || elapsedMs < TimeUnit.SECONDS.toMillis(VEHICLE_RECONNECT_SECONDS)
                || !vehicleReconnectQueued.compareAndSet(false, true)) {
            return;
        }
        boolean posted = mainHandler.post(() -> {
            try {
                connectVehicleBridge();
            } finally {
                vehicleReconnectQueued.set(false);
            }
        });
        if (!posted) {
            vehicleReconnectQueued.set(false);
        }
    }

    private void publish() {
        MonitorUiState state = currentState();
        mainHandler.post(() -> {
            for (UiCallback callback : callbacks) {
                callback.onStateChanged(state);
            }
        });
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "高地 AI 风险监控",
                    NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("保持 P5 车端与高地 AI 后端的风险监控连接");
            getSystemService(NotificationManager.class).createNotificationChannel(channel);
        }
    }

    @SuppressWarnings("deprecation")
    private Notification buildNotification(String text) {
        Intent activityIntent = new Intent(this, MainActivity.class);
        int pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 0, activityIntent, pendingFlags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setSmallIcon(R.drawable.ic_highground)
                .setContentTitle("高地 AI · P5 车端")
                .setContentText(text)
                .setContentIntent(pendingIntent)
                .setOngoing(monitoring)
                .build();
    }

    private void updateNotification(String text) {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        manager.notify(NOTIFICATION_ID, buildNotification(text));
    }

    private static String safeMessage(Throwable error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty()
                ? error.getClass().getSimpleName()
                : message;
    }

    private final VehicleBridge.Listener vehicleListener = new VehicleBridge.Listener() {
        @Override
        public void onConnectionChanged(boolean connected, String detail) {
            xuiStatus = "P5 XUI：" + detail;
            publish();
        }

        @Override
        public void onSpeedChanged(float speed) {
            speedKmh = speed;
            publish();
        }

        @Override
        public void onGearChanged(int gear) {
            rawGearCode = gear;
            publish();
        }

        @Override
        public void onWeatherChanged(String value) {
            weather = value == null || value.trim().isEmpty() ? "—" : value;
            publish();
        }

        @Override
        public void onVehicleError(String operation, Throwable error) {
            xuiStatus = "P5 XUI：" + operation
                    + (error == null ? "" : "：" + safeMessage(error));
            publish();
        }
    };

    public interface UiCallback {
        void onStateChanged(MonitorUiState state);
    }

    public final class LocalBinder extends Binder {
        public HighGroundMonitorService service() {
            return HighGroundMonitorService.this;
        }

        public void register(UiCallback callback) {
            callbacks.add(callback);
            callback.onStateChanged(currentState());
        }

        public void unregister(UiCallback callback) {
            callbacks.remove(callback);
        }
    }
}
