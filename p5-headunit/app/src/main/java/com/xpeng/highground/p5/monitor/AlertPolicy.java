package com.xpeng.highground.p5.monitor;

import com.xpeng.highground.p5.backend.DecisionSnapshot;

public final class AlertPolicy {
    private AlertPolicy() {
    }

    public static AlertAction evaluate(DecisionSnapshot snapshot) {
        switch (snapshot.decision) {
            case "EMERGENCY_STOP":
                return new AlertAction(
                        VoicePriority.URGENT,
                        true,
                        "高地 AI 紧急告警。车辆或环境安全条件异常，请不要移动车辆，立即按现场应急流程处理。");
            case "NO_GO":
                return new AlertAction(
                        VoicePriority.URGENT,
                        true,
                        "高地 AI 检测到内涝风险，但当前不满足安全移车条件。请勿移车，并立即联系现场应急人员。");
            case "MIGRATE_NOW":
                return new AlertAction(
                        VoicePriority.URGENT,
                        true,
                        "高地 AI 检测到内涝风险。请驾驶员确认周边安全后人工移车。本应用不会控制车辆行驶。");
            case "PREPARE":
                return new AlertAction(
                        VoicePriority.IMPORTANT,
                        true,
                        "高地 AI 提醒：积水风险正在上升，请准备人工检查车辆和干燥路线。");
            case "WATCH":
                return new AlertAction(
                        VoicePriority.NORMAL,
                        false,
                        "高地 AI 提醒：当前进入暴雨观察状态，请关注积水变化。");
            case "VERIFY_ONLY":
                return new AlertAction(
                        VoicePriority.IMPORTANT,
                        false,
                        "高地 AI 发现传感器数据需要人工复核，暂不执行任何移车操作。");
            case "STAY":
            default:
                return new AlertAction(VoicePriority.NONE, false, "");
        }
    }

    public enum VoicePriority {
        NONE,
        NORMAL,
        IMPORTANT,
        URGENT
    }

    public static final class AlertAction {
        public final VoicePriority voicePriority;
        public final boolean warningLight;
        public final String message;

        AlertAction(VoicePriority voicePriority, boolean warningLight, String message) {
            this.voicePriority = voicePriority;
            this.warningLight = warningLight;
            this.message = message;
        }
    }
}
