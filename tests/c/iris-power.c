/* SPDX-License-Identifier: MIT
 * HW-08: actual VPU power code with mocked resource acquisition/release.
 */
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <limits.h>
#include <stddef.h>
#include <stdio.h>

typedef unsigned int u32;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define dev_err(...) ((void)0)
struct iris_core;
struct vpu_ops {
    int (*power_on_controller)(struct iris_core *);
    int (*power_on_hw)(struct iris_core *);
    void (*power_off_controller)(struct iris_core *);
    void (*power_off_hw)(struct iris_core *);
};
struct platform { bool legacy_vpu5; const struct vpu_ops *vpu_ops; };
struct iris_core {
    struct platform *iris_platform_data;
    struct { u32 clk_freq; } power;
    void *dev;
    int irq, intr_status;
    bool vpu_powered, llcc_active, syscache_set;
    void *llcc_slices[2];
};
static int ctrl_refs, hw_refs, irq_depth, bw_refs, opp_refs;
static int ctrl_error, hw_error, opp_error, bw_error;
static int ctrl_off_calls, hw_off_calls, hard_sync_calls, slice_refs, slice_active;
static bool expect_hard_drain;
static bool iris_vpu_uses_legacy_vpu5(struct iris_core *c)
{ return c->iris_platform_data->legacy_vpu5; }
static int iris_set_icc_bw(struct iris_core *c, int rate)
{ (void)c; (void)rate; if (!bw_error) bw_refs++; return bw_error; }
static void iris_unset_icc_bw(struct iris_core *c)
{ (void)c; assert(bw_refs == 1); bw_refs--; }
static int power_on_controller(struct iris_core *c)
{ (void)c; if (!ctrl_error) ctrl_refs++; return ctrl_error; }
static int power_on_hw(struct iris_core *c)
{ (void)c; if (!hw_error) hw_refs++; return hw_error; }
static void power_off_controller(struct iris_core *c)
{ (void)c; assert(ctrl_refs == 1); ctrl_refs--; ctrl_off_calls++; }
static void power_off_hw(struct iris_core *c)
{
    (void)c;
    assert(hw_refs == 1);
    if (expect_hard_drain) assert(hard_sync_calls > 0);
    hw_refs--; hw_off_calls++;
}
static int iris_opp_set_rate(void *dev, unsigned long rate)
{
    (void)dev;
    if (!rate) { opp_refs = 0; return 0; }
    if (opp_error) return opp_error;
    opp_refs = 1;
    return 0;
}
static void iris_vpu_set_preset_registers(struct iris_core *c)
{ (void)c; assert(ctrl_refs == 1 && hw_refs == 1 && opp_refs == 1); }
static void iris_vpu_interrupt_init(struct iris_core *c) { (void)c; }
static inline void iris_vpu_trace_pm(struct iris_core *c, const char *phase)
{ (void)c; (void)phase; }
static void enable_irq(int irq) { (void)irq; assert(irq_depth > 0); irq_depth--; }
static void disable_irq(int irq) { (void)irq; irq_depth++; }
static void disable_irq_nosync(int irq) { (void)irq; irq_depth++; }
static void synchronize_hardirq(int irq) { (void)irq; hard_sync_calls++; }
static bool iris_vpu_watchdog(struct iris_core *c, int status)
{ (void)c; return status != 0; }
static void llcc_slice_deactivate(void *slice)
{ assert(slice && slice_active > 0); slice_active--; }
static void llcc_slice_putd(void *slice)
{ assert(slice && slice_refs > 0); slice_refs--; }

#include "iris_power_under_test.inc"

static const struct vpu_ops ops = {
    .power_on_controller = power_on_controller, .power_on_hw = power_on_hw,
    .power_off_controller = power_off_controller, .power_off_hw = power_off_hw,
};
static struct platform platform = { .legacy_vpu5 = true, .vpu_ops = &ops };
static struct iris_core reset(void)
{
    ctrl_refs = hw_refs = bw_refs = opp_refs = 0; irq_depth = 1;
    ctrl_error = hw_error = opp_error = bw_error = 0;
    ctrl_off_calls = hw_off_calls = hard_sync_calls = slice_refs = slice_active = 0;
    expect_hard_drain = false;
    return (struct iris_core){ .iris_platform_data = &platform };
}
static void clean(struct iris_core *core)
{
    assert(ctrl_refs == 0 && hw_refs == 0 && bw_refs == 0 && opp_refs == 0);
    assert(!core->vpu_powered && irq_depth == 1);
    assert(slice_refs == 0 && slice_active == 0);
}
int main(void)
{
    for (int legacy = 0; legacy < 2; legacy++) {
        platform.legacy_vpu5 = legacy;
        for (int failure = 0; failure < 4; failure++) {
            struct iris_core core = reset();
            if (failure == 0) bw_error = -EIO;
            if (failure == 1) ctrl_error = -EIO;
            if (failure == 2) hw_error = -EIO;
            if (failure == 3) opp_error = -EIO;
            assert(iris_vpu_power_on(&core) == -EIO);
            clean(&core);
            iris_vpu_power_off(&core); clean(&core);
        }
        struct iris_core core = reset();
        assert(iris_vpu_power_on(&core) == 0 && core.vpu_powered);
        assert(irq_depth == 0);
        assert(iris_vpu_power_on(&core) == -EALREADY);
        assert(ctrl_refs == 1 && hw_refs == 1 && irq_depth == 0);
        core.llcc_active = core.syscache_set = true;
        core.llcc_slices[0] = &core; core.llcc_slices[1] = &core;
        slice_refs = slice_active = 2;
        iris_vpu_power_off(&core); clean(&core);
        assert(!core.llcc_active && !core.syscache_set);
        assert(!core.llcc_slices[0] && !core.llcc_slices[1]);
        iris_vpu_power_off(&core); clean(&core);
        assert(ctrl_off_calls == 1 && hw_off_calls == 1);
    }
    platform.legacy_vpu5 = true;
    struct iris_core core = reset();
    assert(iris_vpu_power_on(&core) == 0);
    expect_hard_drain = true;
    iris_vpu5_pm_power_off(&core); clean(&core);
    iris_vpu5_pm_power_off(&core); clean(&core);
    assert(hard_sync_calls == 1 && ctrl_off_calls == 1 && hw_off_calls == 1);
    assert(iris_vpu_power_on(&core) == 0 && irq_depth == 0);
    core.intr_status = 1; /* Hard ISR already masked the watchdog IRQ. */
    irq_depth = 1;
    iris_vpu5_pm_power_off(&core); clean(&core);
    puts("HW-08 Iris power fault-injection and duplicate-call checks passed");
    return 0;
}
