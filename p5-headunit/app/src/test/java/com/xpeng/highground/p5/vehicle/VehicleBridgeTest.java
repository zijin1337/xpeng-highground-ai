package com.xpeng.highground.p5.vehicle;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class VehicleBridgeTest {
    @Test
    public void allRequestedCapabilitiesStopReconnects() {
        assertTrue(VehicleBridge.hasAllCapabilities(true, true, true));
    }

    @Test
    public void anyMissingCapabilityKeepsReconnectsEnabled() {
        assertFalse(VehicleBridge.hasAllCapabilities(false, true, true));
        assertFalse(VehicleBridge.hasAllCapabilities(true, false, true));
        assertFalse(VehicleBridge.hasAllCapabilities(true, true, false));
        assertFalse(VehicleBridge.hasAllCapabilities(false, false, false));
    }
}
