package com.xpeng.highground.p5.backend;

import org.json.JSONException;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public class DecisionSnapshotTest {
    @Test
    public void parsesBackendEventDetail() throws Exception {
        String json = "{"
                + "\"event_id\":\"evt_01\","
                + "\"received_at\":\"2026-07-18T01:02:03Z\","
                + "\"telemetry\":{\"environment\":{\"rainfall_mm_h\":96,\"water_level_cm\":14}},"
                + "\"result\":{"
                + "\"decision\":\"MIGRATE_NOW\","
                + "\"label\":\"立即迁移\","
                + "\"risk_level\":\"CRITICAL\","
                + "\"reason\":\"安全窗口正在缩短\""
                + "}}";

        DecisionSnapshot snapshot = DecisionSnapshot.parse(json);

        assertEquals("evt_01", snapshot.eventId);
        assertEquals("MIGRATE_NOW", snapshot.decision);
        assertEquals("CRITICAL", snapshot.riskLevel);
        assertEquals(96.0, snapshot.rainfallMmH, 0.001);
        assertEquals(14.0, snapshot.waterLevelCm, 0.001);
    }

    @Test
    public void rejectsUnknownDecisionInsteadOfSilentlyClearingAnAlert() {
        String json = "{"
                + "\"event_id\":\"evt_unknown\","
                + "\"result\":{"
                + "\"decision\":\"UNRECOGNIZED\","
                + "\"risk_level\":\"CRITICAL\""
                + "}}";

        assertThrows(JSONException.class, () -> DecisionSnapshot.parse(json));
    }

    @Test
    public void rejectsOutOfRangeEnvironmentReading() {
        String json = "{"
                + "\"event_id\":\"evt_bad_sensor\","
                + "\"telemetry\":{\"environment\":{\"rainfall_mm_h\":50,\"water_level_cm\":-1}},"
                + "\"result\":{"
                + "\"decision\":\"WATCH\","
                + "\"risk_level\":\"MEDIUM\""
                + "}}";

        assertThrows(JSONException.class, () -> DecisionSnapshot.parse(json));
    }

    @Test
    public void rejectsOversizedUiText() {
        String json = "{"
                + "\"event_id\":\"evt_long_reason\","
                + "\"result\":{"
                + "\"decision\":\"WATCH\","
                + "\"risk_level\":\"MEDIUM\","
                + "\"reason\":\"" + repeat('a', 2_001) + "\""
                + "}}";

        assertThrows(JSONException.class, () -> DecisionSnapshot.parse(json));
    }

    @Test
    public void rejectsInvalidOrTimezoneFreeReceivedAt() {
        String invalidDay = validResponseWithReceivedAt("2026-02-30T01:02:03Z");
        String noTimezone = validResponseWithReceivedAt("2026-07-18T01:02:03");

        assertThrows(JSONException.class, () -> DecisionSnapshot.parse(invalidDay));
        assertThrows(JSONException.class, () -> DecisionSnapshot.parse(noTimezone));
    }

    @Test
    public void acceptsBackendMicrosecondsAndUtcOffset() throws Exception {
        DecisionSnapshot snapshot = DecisionSnapshot.parse(
                validResponseWithReceivedAt("2026-07-18T12:06:06.231837+00:00"));

        assertEquals("2026-07-18T12:06:06.231837+00:00", snapshot.receivedAt);
    }

    private static String validResponseWithReceivedAt(String receivedAt) {
        return "{"
                + "\"event_id\":\"evt_time\","
                + "\"received_at\":\"" + receivedAt + "\","
                + "\"result\":{"
                + "\"decision\":\"STAY\","
                + "\"risk_level\":\"LOW\""
                + "}}";
    }

    private static String repeat(char value, int count) {
        StringBuilder result = new StringBuilder(count);
        for (int index = 0; index < count; index++) {
            result.append(value);
        }
        return result.toString();
    }
}
