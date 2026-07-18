package com.xpeng.highground.p5.vehicle;

public final class VehicleBridgeFactory {
    private static final String XUI_MANAGER = "com.xiaopeng.xuimanager.XUIManager";
    private static final String P5_BRIDGE = "com.xpeng.highground.p5.vehicle.XpengP5Bridge";

    private VehicleBridgeFactory() {
    }

    public static VehicleBridge create() {
        try {
            Class.forName(XUI_MANAGER);
            Object bridge = Class.forName(P5_BRIDGE).getDeclaredConstructor().newInstance();
            return (VehicleBridge) bridge;
        } catch (Throwable error) {
            return new UnavailableVehicleBridge(
                    "未检测到小鹏 P5 XUI 运行时；后端监控仍可使用，车辆状态、语音和灯光不可用。"
                            + " 原因：" + error.getClass().getSimpleName());
        }
    }
}
