/* SPDX-License-Identifier: MIT
 * HW-08: zero frequency must release OPP votes rather than choose the minimum.
 * The kernel's __free cleanup remains outside this control-flow mock.
 */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

#define __free(name)
#define IS_ERR(p) ((intptr_t)(p) < 0)
#define PTR_ERR(p) ((int)(intptr_t)(p))
struct device { int unused; };
struct dev_pm_opp { int unused; };
static struct dev_pm_opp selected;
static struct dev_pm_opp *applied;
static int lookups, sets, lookup_error, set_error;
static struct dev_pm_opp *devfreq_recommended_opp(struct device *dev,
                                                unsigned long *freq,
                                                unsigned int flags)
{
    (void)dev; (void)flags;
    lookups++;
    if (lookup_error) return (struct dev_pm_opp *)(intptr_t)lookup_error;
    if (!*freq) *freq = 200000000; /* The real ceil lookup chooses the minimum. */
    return &selected;
}
static int dev_pm_opp_set_opp(struct device *dev, struct dev_pm_opp *opp)
{
    (void)dev;
    applied = opp; sets++;
    return set_error;
}

#include "iris_opp_under_test.inc"

int main(void)
{
    struct device dev;
    applied = &selected;
    assert(iris_opp_set_rate(&dev, 0) == 0);
    assert(lookups == 0 && sets == 1 && applied == NULL);
    set_error = -EIO;
    assert(iris_opp_set_rate(&dev, 0) == -EIO && lookups == 0);
    set_error = 0;
    assert(iris_opp_set_rate(&dev, 533000000) == 0 && applied == &selected);
    assert(lookups == 1 && sets == 3);
    lookup_error = -ERANGE;
    assert(iris_opp_set_rate(&dev, 533000000) == -ERANGE && sets == 3);
    puts("HW-08 Iris zero-OPP release and error-propagation checks passed");
    return 0;
}
