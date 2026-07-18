package com.xpeng.highground.p5.vehicle;

import com.xpeng.highground.p5.monitor.AlertPolicy;

final class UnavailableVehicleBridge implements VehicleBridge {
    private final String reason;

    UnavailableVehicleBridge(String reason) {
        this.reason = reason;
    }

    @Override
    public void connect(Listener listener) {
        listener.onConnectionChanged(false, reason);
    }

    @Override
    public boolean isAvailable() {
        return false;
    }

    @Override
    public boolean needsReconnect() {
        return true;
    }

    @Override
    public String capabilityStatus() {
        return reason;
    }

    @Override
    public void speak(AlertPolicy.VoicePriority priority, String text) throws VehicleBridgeException {
        throw new VehicleBridgeException(reason);
    }

    @Override
    public void setWarningLighting(boolean enabled) throws VehicleBridgeException {
        throw new VehicleBridgeException(reason);
    }

    @Override
    public void close() {
    }
}
