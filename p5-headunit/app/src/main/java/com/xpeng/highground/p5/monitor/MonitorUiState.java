package com.xpeng.highground.p5.monitor;

import com.xpeng.highground.p5.backend.DecisionSnapshot;

public final class MonitorUiState {
    public final boolean monitoring;
    public final String monitorStatus;
    public final String xuiStatus;
    public final Float speedKmh;
    public final Integer rawGearCode;
    public final String weather;
    public final DecisionSnapshot latestDecision;

    public MonitorUiState(
            boolean monitoring,
            String monitorStatus,
            String xuiStatus,
            Float speedKmh,
            Integer rawGearCode,
            String weather,
            DecisionSnapshot latestDecision) {
        this.monitoring = monitoring;
        this.monitorStatus = monitorStatus;
        this.xuiStatus = xuiStatus;
        this.speedKmh = speedKmh;
        this.rawGearCode = rawGearCode;
        this.weather = weather;
        this.latestDecision = latestDecision;
    }
}
