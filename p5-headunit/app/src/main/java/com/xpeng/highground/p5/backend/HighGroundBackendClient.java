package com.xpeng.highground.p5.backend;

import org.json.JSONException;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public final class HighGroundBackendClient {
    private static final int TIMEOUT_MS = 6_000;
    private static final int MAX_RESPONSE_CHARS = 256 * 1024;

    public DecisionSnapshot fetchLatest(BackendConfig config) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(config.latestDecisionUrl()).openConnection();
        try {
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(TIMEOUT_MS);
            connection.setReadTimeout(TIMEOUT_MS);
            connection.setUseCaches(false);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Cache-Control", "no-store");
            connection.setRequestProperty("X-API-Key", config.apiKey);
            connection.setRequestProperty("User-Agent", "highground-p5/0.1");

            int status = connection.getResponseCode();
            if (status == 404) {
                throw new BackendException(status, "后端尚无该车辆的决策");
            }
            if (status == 401) {
                throw new BackendException(status, "后端拒绝了 X-API-Key");
            }
            if (status == 410) {
                throw new BackendException(status, "最新决策已过期，等待新遥测");
            }
            if (status < 200 || status >= 300) {
                throw new BackendException(status, "后端返回 HTTP " + status);
            }
            String body = readBody(connection.getInputStream());
            try {
                return DecisionSnapshot.parse(body);
            } catch (JSONException error) {
                throw new IOException("后端响应不是有效的高地 AI 决策", error);
            }
        } finally {
            connection.disconnect();
        }
    }

    private static String readBody(InputStream stream) throws IOException {
        if (stream == null) {
            return "";
        }
        StringBuilder body = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            char[] buffer = new char[4096];
            int count;
            while ((count = reader.read(buffer)) != -1) {
                if (body.length() + count > MAX_RESPONSE_CHARS) {
                    throw new IOException("后端响应超过 256 KiB 限制");
                }
                body.append(buffer, 0, count);
            }
        }
        return body.toString();
    }

    public static final class BackendException extends IOException {
        public final int statusCode;

        BackendException(int statusCode, String message) {
            super(message);
            this.statusCode = statusCode;
        }
    }
}
