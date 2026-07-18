package com.xpeng.highground.p5.backend;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public class BackendConfigTest {
    @Test
    public void buildsEncodedLatestDecisionUrl() {
        BackendConfig config = new BackendConfig(
                "https://example.com/",
                "0123456789abcdef",
                "garage:01",
                "p5_01");

        config.validate(false);

        assertEquals(
                "https://example.com/api/v1/decisions/latest?site_id=garage%3A01&vehicle_id=p5_01",
                config.latestDecisionUrl());
    }

    @Test
    public void rejectsHttpForRelease() {
        BackendConfig config = new BackendConfig(
                "http://192.168.1.9:8000",
                "0123456789abcdef",
                "garage-01",
                "p5-01");

        assertThrows(IllegalArgumentException.class, () -> config.validate(false));
        config.validate(true);
    }

    @Test
    public void rejectsInvalidIdentifiers() {
        BackendConfig config = new BackendConfig(
                "https://example.com",
                "0123456789abcdef",
                "garage 01",
                "p5-01");

        assertThrows(IllegalArgumentException.class, () -> config.validate(false));
    }

    @Test
    public void rejectsCredentialsQueryAndFragmentInBaseUrl() {
        BackendConfig credentials = new BackendConfig(
                "https://user:secret@example.com",
                "0123456789abcdef",
                "garage-01",
                "p5-01");
        BackendConfig query = new BackendConfig(
                "https://example.com?redirect=attacker",
                "0123456789abcdef",
                "garage-01",
                "p5-01");
        BackendConfig fragment = new BackendConfig(
                "https://example.com/#unsafe",
                "0123456789abcdef",
                "garage-01",
                "p5-01");

        assertThrows(IllegalArgumentException.class, () -> credentials.validate(false));
        assertThrows(IllegalArgumentException.class, () -> query.validate(false));
        assertThrows(IllegalArgumentException.class, () -> fragment.validate(false));
    }
}
