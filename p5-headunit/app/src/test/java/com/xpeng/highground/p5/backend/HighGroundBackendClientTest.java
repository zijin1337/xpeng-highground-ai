package com.xpeng.highground.p5.backend;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

import java.io.IOException;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;

public class HighGroundBackendClientTest {
    private static final String API_KEY = "0123456789abcdef";

    private MockWebServer server;

    @Before
    public void startServer() throws IOException {
        server = new MockWebServer();
        server.start();
    }

    @After
    public void stopServer() throws IOException {
        if (server != null) {
            server.shutdown();
        }
    }

    @Test
    public void sendsApiKeyAndParsesLiveHttpResponse() throws Exception {
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .addHeader("Content-Type", "application/json; charset=utf-8")
                .setBody("{"
                        + "\"event_id\":\"evt_http\","
                        + "\"received_at\":\"2026-07-18T01:02:03Z\","
                        + "\"telemetry\":{\"environment\":{\"rainfall_mm_h\":50,\"water_level_cm\":5}},"
                        + "\"result\":{"
                        + "\"decision\":\"WATCH\","
                        + "\"label\":\"观察\","
                        + "\"risk_level\":\"MEDIUM\","
                        + "\"reason\":\"雨量达到观察阈值\""
                        + "}}"));
        BackendConfig config = new BackendConfig(
                server.url("/").toString(),
                API_KEY,
                "garage-01",
                "p5-01");
        config.validate(true);

        DecisionSnapshot snapshot = new HighGroundBackendClient().fetchLatest(config);
        RecordedRequest request = server.takeRequest();

        assertEquals("evt_http", snapshot.eventId);
        assertEquals("WATCH", snapshot.decision);
        assertEquals("GET", request.getMethod());
        assertEquals(
                "/api/v1/decisions/latest?site_id=garage-01&vehicle_id=p5-01",
                request.getPath());
        assertEquals(API_KEY, request.getHeader("X-API-Key"));
        assertEquals("application/json", request.getHeader("Accept"));
    }

    @Test
    public void rejectsRedirectWithoutForwardingApiKey() {
        server.enqueue(new MockResponse()
                .setResponseCode(302)
                .addHeader("Location", server.url("/unexpected-target")));
        BackendConfig config = new BackendConfig(
                server.url("/").toString(),
                API_KEY,
                "garage-01",
                "p5-01");
        config.validate(true);

        HighGroundBackendClient.BackendException error = assertThrows(
                HighGroundBackendClient.BackendException.class,
                () -> new HighGroundBackendClient().fetchLatest(config));

        assertEquals(302, error.statusCode);
        assertEquals(1, server.getRequestCount());
    }

    @Test
    public void reportsGoneWhenLatestDecisionIsStale() {
        String oversizedErrorBody = new String(new char[300 * 1024]).replace('\0', 'x');
        server.enqueue(new MockResponse()
                .setResponseCode(410)
                .addHeader("Content-Type", "application/json; charset=utf-8")
                .setBody(oversizedErrorBody));
        BackendConfig config = new BackendConfig(
                server.url("/").toString(),
                API_KEY,
                "garage-01",
                "p5-01");
        config.validate(true);

        HighGroundBackendClient.BackendException error = assertThrows(
                HighGroundBackendClient.BackendException.class,
                () -> new HighGroundBackendClient().fetchLatest(config));

        assertEquals(410, error.statusCode);
        assertEquals("最新决策已过期，等待新遥测", error.getMessage());
        assertEquals(1, server.getRequestCount());
    }
}
