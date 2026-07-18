package com.xpeng.highground.p5.vehicle;

import com.xpeng.highground.p5.monitor.AlertPolicy;

public interface VehicleBridge {
    void connect(Listener listener);

    boolean isAvailable();

    String capabilityStatus();

    void speak(AlertPolicy.VoicePriority priority, String text) throws VehicleBridgeException;

    void setWarningLighting(boolean enabled) throws VehicleBridgeException;

    void close();

    interface Listener {
        void onConnectionChanged(boolean connected, String detail);

        void onSpeedChanged(float speedKmh);

        void onGearChanged(int rawGearCode);

        void onWeatherChanged(String weather);

        void onVehicleError(String operation, Throwable error);
    }

    final class VehicleBridgeException extends Exception {
        public VehicleBridgeException(String message, Throwable cause) {
            super(message, cause);
        }

        public VehicleBridgeException(String message) {
            super(message);
        }
    }
}
