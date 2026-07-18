package com.xpeng.highground.p5.backend;

import java.io.UnsupportedEncodingException;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URLEncoder;
import java.util.regex.Pattern;

public final class BackendConfig {
    private static final Pattern IDENTIFIER = Pattern.compile("^[A-Za-z0-9_.:-]{1,80}$");

    public final String apiBaseUrl;
    public final String apiKey;
    public final String siteId;
    public final String vehicleId;

    public BackendConfig(String apiBaseUrl, String apiKey, String siteId, String vehicleId) {
        this.apiBaseUrl = stripTrailingSlash(apiBaseUrl == null ? "" : apiBaseUrl.trim());
        this.apiKey = apiKey == null ? "" : apiKey.trim();
        this.siteId = siteId == null ? "" : siteId.trim();
        this.vehicleId = vehicleId == null ? "" : vehicleId.trim();
    }

    public void validate(boolean allowCleartextHttp) {
        final URI uri;
        try {
            uri = new URI(apiBaseUrl);
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("API 地址格式无效");
        }
        String scheme = uri.getScheme();
        if (scheme == null || uri.getHost() == null) {
            throw new IllegalArgumentException("API 地址必须包含协议和主机名");
        }
        if (uri.getRawUserInfo() != null) {
            throw new IllegalArgumentException("API 地址不得内嵌用户名或密码");
        }
        if (uri.getRawQuery() != null || uri.getRawFragment() != null) {
            throw new IllegalArgumentException("API 地址不得包含查询参数或片段");
        }
        if (!"https".equalsIgnoreCase(scheme)
                && !(allowCleartextHttp && "http".equalsIgnoreCase(scheme))) {
            throw new IllegalArgumentException(
                    allowCleartextHttp ? "API 地址只支持 HTTPS 或调试用 HTTP" : "正式版只允许 HTTPS");
        }
        if (apiKey.length() < 16) {
            throw new IllegalArgumentException("X-API-Key 至少需要 16 个字符");
        }
        if (!IDENTIFIER.matcher(siteId).matches()) {
            throw new IllegalArgumentException("场站 ID 只能使用字母、数字、点、冒号、下划线或连字符");
        }
        if (!IDENTIFIER.matcher(vehicleId).matches()) {
            throw new IllegalArgumentException("车辆 ID 只能使用字母、数字、点、冒号、下划线或连字符");
        }
    }

    public String latestDecisionUrl() {
        try {
            return apiBaseUrl
                    + "/api/v1/decisions/latest?site_id="
                    + URLEncoder.encode(siteId, "UTF-8")
                    + "&vehicle_id="
                    + URLEncoder.encode(vehicleId, "UTF-8");
        } catch (UnsupportedEncodingException impossible) {
            throw new AssertionError("UTF-8 must be available", impossible);
        }
    }

    private static String stripTrailingSlash(String value) {
        int end = value.length();
        while (end > 0 && value.charAt(end - 1) == '/') {
            end -= 1;
        }
        return value.substring(0, end);
    }
}
