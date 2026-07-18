package com.xpeng.highground.p5.backend;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class DecisionSnapshot {
    private static final int MAX_EVENT_ID_LENGTH = 128;
    // Client-side bounds protect the UI and memory; backend business rules remain authoritative.
    private static final int MAX_RECEIVED_AT_LENGTH = 64;
    private static final int MAX_LABEL_LENGTH = 160;
    private static final int MAX_REASON_LENGTH = 2_000;
    private static final Pattern ISO_8601_TIMESTAMP = Pattern.compile(
            "^(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})"
                    + "(?:\\.\\d{1,9})?(?:Z|([+-])(\\d{2}):(\\d{2}))$");

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
        String receivedAt = optionalString(root, "received_at", "", MAX_RECEIVED_AT_LENGTH);
        String label = optionalString(result, "label", decision, MAX_LABEL_LENGTH);
        String reason = optionalString(result, "reason", "", MAX_REASON_LENGTH);
        if (eventId.isEmpty() || eventId.length() > MAX_EVENT_ID_LENGTH) {
            throw new JSONException("event_id 长度无效");
        }
        if (!isKnownDecision(decision)) {
            throw new JSONException("未知 decision：" + decision);
        }
        if (!isKnownRiskLevel(riskLevel)) {
            throw new JSONException("未知 risk_level：" + riskLevel);
        }
        validateReceivedAt(receivedAt);
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
                receivedAt,
                decision,
                label,
                riskLevel,
                reason,
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

    private static String optionalString(
            JSONObject object,
            String name,
            String fallback,
            int maximumLength) throws JSONException {
        Object value = object.opt(name);
        if (value == null || value == JSONObject.NULL) {
            return fallback;
        }
        if (!(value instanceof String)) {
            throw new JSONException(name + " 必须是字符串");
        }
        String text = ((String) value).trim();
        if (text.length() > maximumLength) {
            throw new JSONException(name + " 过长");
        }
        return text.isEmpty() && !fallback.isEmpty() ? fallback : text;
    }

    private static void validateReceivedAt(String value) throws JSONException {
        if (value.isEmpty()) {
            return;
        }
        Matcher matcher = ISO_8601_TIMESTAMP.matcher(value);
        if (!matcher.matches()) {
            throw new JSONException("received_at 不是带时区的 ISO-8601 时间");
        }
        int year = integer(matcher, 1);
        int month = integer(matcher, 2);
        int day = integer(matcher, 3);
        int hour = integer(matcher, 4);
        int minute = integer(matcher, 5);
        int second = integer(matcher, 6);
        if (year < 1 || month < 1 || month > 12 || day < 1
                || day > daysInMonth(year, month) || hour > 23 || minute > 59 || second > 59) {
            throw new JSONException("received_at 日期或时间无效");
        }
        if (matcher.group(7) != null) {
            int offsetHour = integer(matcher, 8);
            int offsetMinute = integer(matcher, 9);
            if (offsetHour > 18 || offsetMinute > 59
                    || (offsetHour == 18 && offsetMinute != 0)) {
                throw new JSONException("received_at 时区偏移无效");
            }
        }
    }

    private static int integer(Matcher matcher, int group) {
        return Integer.parseInt(matcher.group(group));
    }

    private static int daysInMonth(int year, int month) {
        switch (month) {
            case 2:
                return (year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)) ? 29 : 28;
            case 4:
            case 6:
            case 9:
            case 11:
                return 30;
            default:
                return 31;
        }
    }
}
