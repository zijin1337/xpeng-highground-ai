package com.xpeng.highground.p5.vehicle;

import com.xiaopeng.xuimanager.XUIManager;
import com.xiaopeng.xuimanager.ambientlight.AmbientLightManager;
import com.xiaopeng.xuimanager.contextinfo.ContextInfoManager;
import com.xiaopeng.xuimanager.smart.SmartManager;
import com.xpeng.highground.p5.monitor.AlertPolicy;

/**
 * The only class that directly references the non-public P5 framework.
 *
 * It is compileOnly and loaded reflectively by VehicleBridgeFactory, so the rest of the app still
 * starts on a non-P5 Android device and reports a truthful capability failure.
 */
public final class XpengP5Bridge implements VehicleBridge {
    private XUIManager xuiManager;
    private SmartManager smartManager;
    private AmbientLightManager ambientLightManager;
    private ContextInfoManager contextInfoManager;
    private ContextInfoManager.ContextNaviInfoEventListener contextListener;
    private boolean registered;
    private boolean available;
    private String status = "P5 XUI 尚未连接";
    private boolean lightingSnapshotTaken;
    private boolean previousLightOpen;
    private String previousLightEffect;

    @Override
    public synchronized void connect(Listener listener) {
        close();
        try {
            xuiManager = XUIManager.getInstance();
            smartManager = as(
                    xuiManager.getService(XUIManager.SMART_SERVICE),
                    SmartManager.class);
            ambientLightManager = as(
                    xuiManager.getService(XUIManager.AMBIENTLIGHT_SERVICE),
                    AmbientLightManager.class);
            contextInfoManager = as(
                    xuiManager.getService(XUIManager.CONTEXTINFO_SERVICE),
                    ContextInfoManager.class);

            contextListener = new ContextInfoManager.ContextNaviInfoEventListener() {
                @Override
                public void onCarSpeed(float speed) {
                    listener.onSpeedChanged(speed);
                }

                @Override
                public void onGearChanged(int gear) {
                    // The community SDK does not document a stable numeric mapping. Keep it raw.
                    listener.onGearChanged(gear);
                }

                @Override
                public void onWeatherInfo(String weatherInfo) {
                    listener.onWeatherChanged(weatherInfo);
                }

                @Override
                public void onErrorEvent(int errorCode, int operation) {
                    listener.onVehicleError(
                            "ContextInfo error " + errorCode + "/" + operation,
                            null);
                }
            };
            if (contextInfoManager != null) {
                contextInfoManager.registerListener(contextListener);
                registered = true;
            }

            available = smartManager != null || ambientLightManager != null || registered;
            String version;
            try {
                version = XUIManager.getXuiVersion();
            } catch (Throwable ignored) {
                version = "未知";
            }
            status = "P5 XUI " + version
                    + " · 状态读取=" + yesNo(registered)
                    + " · 小P=" + yesNo(smartManager != null)
                    + " · 环境灯=" + yesNo(ambientLightManager != null);
            listener.onConnectionChanged(available, status);
        } catch (Throwable error) {
            available = false;
            status = "P5 XUI 连接或权限校验失败：" + error.getClass().getSimpleName()
                    + safeMessage(error);
            listener.onConnectionChanged(false, status);
            listener.onVehicleError("连接 P5 XUI", error);
        }
    }

    @Override
    public synchronized boolean isAvailable() {
        return available;
    }

    @Override
    public synchronized boolean needsReconnect() {
        return !VehicleBridge.hasAllCapabilities(
                registered,
                smartManager != null,
                ambientLightManager != null);
    }

    @Override
    public synchronized String capabilityStatus() {
        return status;
    }

    @Override
    public synchronized void speak(AlertPolicy.VoicePriority priority, String text)
            throws VehicleBridgeException {
        if (smartManager == null) {
            throw new VehicleBridgeException("P5 小P语音服务不可用或未授权");
        }
        try {
            switch (priority) {
                case URGENT:
                    smartManager.speakByUrgent(text);
                    break;
                case IMPORTANT:
                    smartManager.speakByImportant(text);
                    break;
                case NORMAL:
                    smartManager.speakByNormal(text);
                    break;
                case NONE:
                default:
                    break;
            }
        } catch (Throwable error) {
            throw new VehicleBridgeException("P5 小P语音调用失败", error);
        }
    }

    @Override
    public synchronized void setWarningLighting(boolean enabled) throws VehicleBridgeException {
        if (ambientLightManager == null) {
            throw new VehicleBridgeException("P5 环境灯服务不可用或未授权");
        }
        try {
            if (enabled) {
                if (!lightingSnapshotTaken) {
                    previousLightOpen = ambientLightManager.getAmbientLightOpen();
                    previousLightEffect = ambientLightManager.getAmbientLightEffectType();
                    lightingSnapshotTaken = true;
                }
                ambientLightManager.setAmbientLightOpen(true);
                ambientLightManager.setAmbientLightEffectType(
                        AmbientLightManager.EFFECT_GENTLE_BREATHING);
            } else {
                restoreLighting();
            }
        } catch (Throwable error) {
            throw new VehicleBridgeException("P5 环境灯调用失败", error);
        }
    }

    @Override
    public synchronized void close() {
        if (contextInfoManager != null && contextListener != null && registered) {
            try {
                contextInfoManager.unregisterListener(contextListener);
            } catch (Throwable ignored) {
                // Best-effort cleanup during service shutdown.
            }
        }
        try {
            restoreLighting();
        } catch (Throwable ignored) {
            // Best-effort restore; the app must still be able to stop.
        }
        registered = false;
        available = false;
        contextListener = null;
        contextInfoManager = null;
        ambientLightManager = null;
        smartManager = null;
        xuiManager = null;
    }

    private void restoreLighting() throws Exception {
        if (!lightingSnapshotTaken || ambientLightManager == null) {
            return;
        }
        if (previousLightEffect != null && !previousLightEffect.isEmpty()) {
            ambientLightManager.setAmbientLightEffectType(previousLightEffect);
        }
        ambientLightManager.setAmbientLightOpen(previousLightOpen);
        lightingSnapshotTaken = false;
        previousLightEffect = null;
    }

    private static <T> T as(Object value, Class<T> type) {
        return type.isInstance(value) ? type.cast(value) : null;
    }

    private static String yesNo(boolean value) {
        return value ? "可用" : "不可用";
    }

    private static String safeMessage(Throwable error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty() ? "" : "（" + message + "）";
    }
}
