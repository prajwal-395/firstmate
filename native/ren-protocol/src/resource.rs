//! The resource model: what a job demands of the machine.
//!
//! Modeled from `library/tools/resource_scheduler.py`. The resources are
//! one enumeration; a demand is the scheduler's dict of resource to
//! amount, carried as a map so a demand cannot name a resource the
//! machine does not have.

use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::collections::BTreeMap;

/// The machine's resources (`resource_scheduler.RESOURCES`). Complete:
/// the scheduler admits a job only while every resource stays within its
/// capacity.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ResourceName {
    /// The Resolve instance cursor (capacity 1). The Resolve lease stays
    /// the authority over it and is always taken first.
    ResolveCursor,
    /// Resolve's render engine (capacity 1).
    ResolveRender,
    /// CPU cores.
    Cpu,
    /// The GPU as one tenant: local model inference and a Resolve render
    /// each take all of it (capacity 1).
    Gpu,
    /// Physical memory less the OS reserve.
    RamGb,
    /// I/O weight.
    Disk,
}

/// One job's demand over the named resources. The wire form is the
/// scheduler's JSON object (`{"cpu": 4, "ram_gb": 16}`); the type is the
/// map, so an unknown resource name is a deserialization error rather
/// than a silent entry.
#[derive(Clone, Debug, Default, PartialEq, Eq, JsonSchema)]
#[schemars(with = "BTreeMap<ResourceName, u64>")]
pub struct ResourceDemand(pub BTreeMap<ResourceName, u64>);

impl ResourceDemand {
    /// Whether a held grant already includes everything this demand needs
    /// (`resource_scheduler.covers`).
    pub fn covered_by(&self, held: &ResourceDemand) -> bool {
        self.0
            .iter()
            .all(|(name, amount)| held.0.get(name).is_some_and(|have| have >= amount))
    }
}

impl Serialize for ResourceDemand {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_map(self.0.iter())
    }
}

impl<'de> Deserialize<'de> for ResourceDemand {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let raw = BTreeMap::<ResourceName, u64>::deserialize(deserializer)?;
        Ok(ResourceDemand(raw))
    }
}
