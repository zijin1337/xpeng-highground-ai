package com.xpeng.highground.p5.backend;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

public final class DecisionSnapshot {
    private static final int MAX_EVENT_ID_LENGTH = 128;

    public final String eventId;
    public final String receivedAt;
    public final String decision;
    public final String label;
    public final String riskLevel;
    public final String reason;
    public final double rainfallMmH;
    public final double waterLevelCm;

    public DecisionSnapshot(
            String eventId,
            String receivedAt,
            String decision,
            String label,
            String riskLevel,
            String reason,
            double rainfallMmH,
            double waterLevelCm) {
        this.eventId = eventId;
        this.receivedAt = receivedAt;
        this.decision = decision;
        this.label = label;
        this.riskLevel = riskLevel;
        this.reason = reason;
        this.rainfallMmH = rainfallMmH;
        this.waterLevelCm = waterLevelCm;
    }

    public static DecisionSnapshot parse(String json) throws JSONException {
        JSONObject root = new JSONObject(json);
        JSONObject result = root.getJSONObject("result");
        JSONObject telemetry = root.optJSONObject("telemetry");
        JSONObject environment = telemetry == null ? null : telemetry.optJSONObject("environment");
        String eventId = root.getString("event_id").trim();
        String decision = result.getString("decision").trim();
        String riskLevel = result.getString("risk_level").trim();
        if (eventId.isEmpty() || eventId.length() > MAX_EVENT_ID_LENGTH) {
            throw new JSONException("event_id 长度无效");
        }
        if (!isKnownDecision(decision)) {
            throw new JSONException("未知 decision：" + decision);
        }
        if (!isKnownRiskLevel(riskLevel)) {
            throw new JSONException("未知 risk_level：" + riskLevel);
        }
        double rainfall = environment == null
                ? Double.NaN
                : environment.optDouble("rainfall_mm_h", Double.NaN);
        double waterLevel = environment == null
                ? Double.NaN
                : environment.optDouble("water_level_cm", Double.NaN);
        validateReading("rainfall_mm_h", rainfall, 500);
        validateReading("water_level_cm", waterLevel, 300);
        return new DecisionSnapshot(
                eventId,
                root.optString("received_at", ""),
                decision,
                result.optString("label", decision),
                riskLevel,
                result.optString("reason", ""),
                rainfall,
                waterLevel);
    }

    public String displayText() {
        String water = Double.isNaN(waterLevelCm)
                ? "—"
                : String.format(Locale.US, "%.1f", waterLevelCm);
        String rain = Double.isNaN(rainfallMmH)
                ? "—"
                : String.format(Locale.US, "%.1f", rainfallMmH);
        return label + " · 风险 " + riskLevel + " · 水位 " + water + " cm · 雨量 " + rain + " mm/h\n" + reason;
    }

    private static boolean isKnownDecision(String value) {
        switch (value) {
            case "STAY":
            case "WATCH":
            case "PREPARE":
            case "MIGRATE_NOW":
            case "VERIFY_ONLY":
            case "NO_GO":
            case "EMERGENCY_STOP":
                return true;
            default:
                return false;
        }
    }

    private static boolean isKnownRiskLevel(String value) {
        return "LOW".equals(value)
                || "MEDIUM".equals(value)
                || "HIGH".equals(value)
                || "CRITICAL".equals(value);
    }

    private static void validateReading(String name, double value, double maximum)
            throws JSONException {
        if (!Double.isNaN(value)
                && (Double.isInfinite(value) || value < 0 || value > maximum)) {
            throw new JSONException(name + " 超出后端协议范围");
        }
    }
}
