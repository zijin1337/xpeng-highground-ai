package com.xpeng.highground.p5.monitor;

import com.xpeng.highground.p5.backend.DecisionSnapshot;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class AlertPolicyTest {
    @Test
    public void migrateNowRequiresUrgentHumanDrivingMessage() {
        AlertPolicy.AlertAction action = AlertPolicy.evaluate(snapshot("MIGRATE_NOW", "CRITICAL"));

        assertEquals(AlertPolicy.VoicePriority.URGENT, action.voicePriority);
        assertTrue(action.warningLight);
        assertTrue(action.message.contains("人工移车"));
        assertTrue(action.message.contains("不会控制车辆行驶"));
    }

    @Test
    public void noGoNeverSuggestsMoving() {
        AlertPolicy.AlertAction action = AlertPolicy.evaluate(snapshot("NO_GO", "CRITICAL"));

        assertEquals(AlertPolicy.VoicePriority.URGENT, action.voicePriority);
        assertTrue(action.warningLight);
        assertTrue(action.message.contains("请勿移车"));
    }

    @Test
    public void stayIsSilentAndRestoresLighting() {
        AlertPolicy.AlertAction action = AlertPolicy.evaluate(snapshot("STAY", "LOW"));

        assertEquals(AlertPolicy.VoicePriority.NONE, action.voicePriority);
        assertFalse(action.warningLight);
        assertEquals("", action.message);
    }

    private static DecisionSnapshot snapshot(String decision, String risk) {
        return new DecisionSnapshot(
                "evt-test",
                "2026-07-18T00:00:00Z",
                decision,
                decision,
                risk,
                "test",
                0,
                0);
    }
}
