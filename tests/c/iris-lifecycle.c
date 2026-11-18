/* SPDX-License-Identifier: MIT
 * HW-08 fault injection. Includes real source, with hardware calls mocked.
 * This cannot establish firmware retention, IRQ timing or physical PM success.
 */
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

enum { IRIS_CORE_DEINIT, IRIS_CORE_INIT, IRIS_CORE_ERROR };
struct mutex { bool held; };
struct iris_core;
struct platform { bool legacy_vpu5; };
struct firmware { void (*init_hfi_ops)(struct iris_core *); };
struct sys_ops { int (*sys_restore_resources)(struct iris_core *); };
struct iris_core {
    void *dev;
    struct mutex lock, init_lock;
    struct platform *iris_platform_data;
    struct firmware *iris_firmware_data;
    struct sys_ops *hfi_sys_ops;
    int state, irq, core_init_done;
    bool legacy_pm_suspended;
};
static struct iris_core *current;
static int irq_depth, power_refs, queues, fw_refs, power_calls;
static int power_ret, queue_ret, fw_ret, boot_ret, cache_ret, secure_ret, prep_ret;
static int pm_get_ret, completion_ret, cache_calls;
#define HW_RESPONSE_TIMEOUT_VALUE 1000
#define dev_err(...) ((void)0)
#define lockdep_assert_held(m) assert((m)->held)
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = true; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = false; }
static int pm_runtime_resume_and_get(void *dev) { (void)dev; return pm_get_ret; }
static void pm_runtime_put_sync(void *dev) { (void)dev; }
static void disable_irq(int irq)
{
    (void)irq;
    /* Full teardown must first drain outside the IRQ thread's mutex. */
    assert(!current->lock.held || irq_depth > 0);
    irq_depth++;
}
static void enable_irq(int irq) { (void)irq; assert(irq_depth > 0); irq_depth--; }
static int iris_set_hw_state(struct iris_core *c, bool on)
{ (void)c; return on ? secure_ret : 0; }
static void iris_fw_unload(struct iris_core *c)
{ (void)c; assert(fw_refs == 1); fw_refs--; }
static void iris_vpu_power_off(struct iris_core *c)
{
    assert(c->lock.held && power_refs == 1);
    power_refs--;
    disable_irq(c->irq);
}
static void iris_hfi_queues_deinit(struct iris_core *c)
{ (void)c; assert(queues == 1); assert(irq_depth > 0); queues--; }
static unsigned long msecs_to_jiffies(unsigned long v) { return v; }
static unsigned long wait_for_completion_timeout(int *done, unsigned long timeout)
{ (void)done; (void)timeout; assert(!current->lock.held); return completion_ret; }
static void reinit_completion(int *done) { *done = 0; }
static int iris_hfi_queues_init(struct iris_core *c)
{ (void)c; assert(queues == 0); if (!queue_ret) queues++; return queue_ret; }
static int iris_vpu_power_on(struct iris_core *c)
{
    power_calls++;
    assert(power_refs == 0);
    if (power_ret) return power_ret;
    power_refs++;
    enable_irq(c->irq);
    return 0;
}
static int iris_fw_load(struct iris_core *c)
{ (void)c; if (!fw_ret) fw_refs++; return fw_ret; }
static int iris_vpu_boot_firmware(struct iris_core *c) { (void)c; return boot_ret; }
static int iris_vpu_switch_to_hwmode(struct iris_core *c) { (void)c; return 0; }
static void init_hfi(struct iris_core *c) { (void)c; }
static int iris_hfi_core_init(struct iris_core *c) { (void)c; return 0; }
static int iris_vpu5_prepare_pc(struct iris_core *c) { (void)c; return prep_ret; }
static void iris_vpu5_pm_power_off(struct iris_core *c)
{
    assert(c->lock.held && c->legacy_pm_suspended);
    assert(power_refs == 1);
    power_refs--;
    irq_depth++;
}
static void iris_vpu_trace_pm(struct iris_core *c, const char *phase)
{ (void)c; (void)phase; }
static int restore_resources(struct iris_core *c)
{
    assert(c->lock.held && !c->legacy_pm_suspended && power_refs == 1);
    cache_calls++;
    return cache_ret;
}

#include "iris_core_under_test.inc"
#include "iris_pm_under_test.inc"

static struct iris_core core;
static struct platform platform = { .legacy_vpu5 = true };
static struct firmware firmware = { .init_hfi_ops = init_hfi };
static struct sys_ops sys_ops = { .sys_restore_resources = restore_resources };
static void reset(void)
{
    memset(&core, 0, sizeof(core));
    core.iris_platform_data = &platform;
    core.iris_firmware_data = &firmware;
    core.hfi_sys_ops = &sys_ops;
    current = &core;
    irq_depth = 1; power_refs = queues = fw_refs = power_calls = 0;
    power_ret = queue_ret = fw_ret = boot_ret = cache_ret = secure_ret = prep_ret = 0;
    pm_get_ret = cache_calls = 0; completion_ret = 1;
}
static void initialized(void)
{
    reset();
    assert(iris_core_init(&core) == 0);
    assert(power_refs == 1 && irq_depth == 0 && queues == 1 && fw_refs == 1);
    assert(!core.lock.held && !core.init_lock.held);
}
static void clean(void)
{
    assert(core.state == IRIS_CORE_DEINIT && !core.legacy_pm_suspended);
    assert(power_refs == 0 && queues == 0 && fw_refs == 0 && irq_depth == 1);
    assert(!core.lock.held && !core.init_lock.held);
}
#ifndef CORE_ONLY
static void suspend_ok(void)
{
    mutex_lock(&core.lock);
    assert(iris_vpu5_suspend(&core) == 0);
    mutex_unlock(&core.lock);
    assert(core.legacy_pm_suspended && power_refs == 0 && irq_depth == 1);
    assert(queues == 1 && fw_refs == 1);
}
static int resume(void)
{
    int ret;
    mutex_lock(&core.lock);
    ret = iris_vpu5_resume(&core);
    mutex_unlock(&core.lock);
    return ret;
}
#endif
int main(void)
{
    reset();
    core.state = IRIS_CORE_ERROR;
    assert(iris_core_init(&core) == -EINVAL && core.state == IRIS_CORE_ERROR);
    assert(iris_core_init(&core) == -EINVAL && power_calls == 0);

    initialized();
    assert(iris_core_init(&core) == 0 && power_calls == 1);
    iris_core_deinit(&core); clean();
    iris_core_deinit(&core); clean();

    reset(); fw_ret = -EIO;
    assert(iris_core_init(&core) == -EIO); clean();
    reset(); boot_ret = -ETIME;
    assert(iris_core_init(&core) == -ETIME); clean();
    reset(); power_ret = -EIO;
    assert(iris_core_init(&core) == -EIO); clean();
    reset(); queue_ret = -ENOMEM;
    assert(iris_core_init(&core) == -ENOMEM); clean();

    reset(); completion_ret = 0;
    assert(iris_core_init(&core) == -ETIMEDOUT && core.state == IRIS_CORE_ERROR);
    assert(iris_core_init(&core) == -EINVAL && core.state == IRIS_CORE_ERROR);
    assert(iris_core_init(&core) == -EINVAL && power_calls == 1);
    iris_core_deinit(&core); clean();

#ifndef CORE_ONLY
    initialized(); suspend_ok();
    iris_core_deinit(&core); clean(); /* Already off: never power off twice. */
    assert(iris_core_init(&core) == 0);
    iris_core_deinit(&core); clean();

    initialized(); suspend_ok();
    assert(resume() == 0 && cache_calls == 1 && !core.legacy_pm_suspended);
    assert(power_refs == 1 && irq_depth == 0 && fw_refs == 1 && queues == 1);
    assert(resume() == 0 && power_calls == 2); /* Repeated callback is harmless. */
    iris_core_deinit(&core); clean();

    for (int failure = 0; failure < 4; failure++) {
        initialized(); suspend_ok();
        if (failure == 0) power_ret = -EIO;
        if (failure == 1) secure_ret = -EACCES;
        if (failure == 2) boot_ret = -ETIME;
        if (failure == 3) cache_ret = -ENOMEM;
        assert(resume() < 0 && core.state == IRIS_CORE_ERROR);
        assert(core.legacy_pm_suspended && power_refs == 0 && irq_depth == 1);
        int attempts = power_calls;
        assert(iris_core_init(&core) == -EINVAL && core.state == IRIS_CORE_ERROR);
        assert(iris_core_init(&core) == -EINVAL && power_calls == attempts);
        iris_core_deinit(&core); clean();
    }
    initialized(); prep_ret = -EAGAIN;
    mutex_lock(&core.lock);
    assert(iris_vpu5_suspend(&core) == -EAGAIN);
    mutex_unlock(&core.lock);
    assert(power_refs == 1 && irq_depth == 0 && !core.legacy_pm_suspended);
    iris_core_deinit(&core); clean();
#endif
    puts("HW-08 Iris lifecycle and PM fault-injection checks passed");
    return 0;
}
