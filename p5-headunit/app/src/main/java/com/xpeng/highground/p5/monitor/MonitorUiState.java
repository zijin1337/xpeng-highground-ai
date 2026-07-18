package com.xpeng.highground.p5.monitor;

public final class MonitorUiState {
    public final boolean monitoring;
    public final String monitorStatus;
    public final String xuiStatus;
    public final Float speedKmh;
    public final Integer rawGearCode;
    public final String weather;
    public final String decisionStatus;

    public MonitorUiState(
            boolean monitoring,
            String monitorStatus,
            String xuiStatus,
            Float speedKmh,
            Integer rawGearCode,
            String weather,
            String decisionStatus) {
        this.monitoring = monitoring;
        this.monitorStatus = monitorStatus;
        this.xuiStatus = xuiStatus;
        this.speedKmh = speedKmh;
        this.rawGearCode = rawGearCode;
        this.weather = weather;
        this.decisionStatus = decisionStatus;
    }
}
