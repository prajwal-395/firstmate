//! Which queued job Resolve runs next. Pure: no socket, no clock of its own.
//!
//! A direct port of `library/tools/resolved/scheduler.py` - the same
//! priority bands, the same aging, the same locality tiebreak, the same
//! head-of-line skip within a band. The Python daemon and the Rust daemon
//! must schedule identically, so a client that switches daemons mid-queue
//! sees the same job order.

/// Most urgent first. The plan's ladder: a human, a short read, a short
/// mutation, a build commit, a QA render, a qualification test, an export.
pub const PRIORITIES: [&str; 7] = [
    "interactive",
    "read",
    "mutation",
    "build",
    "qa_render",
    "qualification",
    "export",
];

pub const EXCLUSIVE: &str = "exclusive";
pub const SHARED: &str = "shared";

/// Seconds of waiting that lift a job one priority class.
pub const AGING_SECONDS: f64 = 30.0;

fn rank(priority: &str) -> Option<usize> {
    PRIORITIES.iter().position(|p| *p == priority)
}

/// What the scheduler needs to know about one job.
#[derive(Clone, Debug)]
pub struct Pending {
    pub id: String,
    pub priority: String,
    pub mode: String,
    pub submitted: f64,
    pub project: String,
    pub timeline: String,
    pub executed: bool,
    pub qualification: bool,
    pub locality: String,
    /// The job's DECLARED budget: the seconds it may hold the one
    /// Resolve (`library/tools/resolved/job_budget.py`). The daemon
    /// enforces it by killing a worker that has held the machine past
    /// it, which is the only bound a wedged Resolve call can be given
    /// from outside the process it wedges.
    pub budget_seconds: f64,
}

fn effective_rank(job: &Pending, now: f64) -> f64 {
    let base = rank(&job.priority).unwrap_or(PRIORITIES.len()) as f64;
    base - (now - job.submitted).max(0.0) / AGING_SECONDS
}

/// The sort key for one job: (priority band after aging, locality,
/// arrival, id). A timestamp is never NaN, so the float comparison is
/// total - the Python daemon compares the same tuple.
fn order_key(job: &Pending, now: f64, cursor: (&str, &str)) -> (i64, i64, OrderedF64, String) {
    let locality = if !job.locality.is_empty() {
        job.locality.as_str()
    } else if !job.timeline.is_empty() {
        "timeline"
    } else if !job.project.is_empty() {
        "project"
    } else {
        "none"
    };
    let local = (locality == "timeline"
        && !job.project.is_empty()
        && (job.project.as_str(), job.timeline.as_str()) == cursor)
        || (locality == "project" && !job.project.is_empty() && job.project == cursor.0);
    (
        effective_rank(job, now).floor() as i64,
        if local { 0 } else { 1 },
        OrderedF64(job.submitted),
        job.id.clone(),
    )
}

/// A float that orders total - the timestamp fields are never NaN.
#[derive(Clone, Copy, Debug)]
struct OrderedF64(f64);

impl PartialEq for OrderedF64 {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0
    }
}

impl Eq for OrderedF64 {}

impl PartialOrd for OrderedF64 {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for OrderedF64 {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.0
            .partial_cmp(&other.0)
            .unwrap_or(std::cmp::Ordering::Equal)
    }
}

fn can_start(job: &Pending, running: &[Pending]) -> bool {
    // Exclusive needs Resolve to itself; shared runs beside shared.
    if job.executed && running.iter().any(|other| other.executed) {
        return false;
    }
    if job.mode == EXCLUSIVE {
        return running.is_empty();
    }
    running.iter().all(|other| other.mode == SHARED)
}

/// The job to start now, or None.
///
/// The head of the queue starts if it can. If it cannot (a writer behind
/// running readers), the next job in the SAME priority band that can start
/// is skipped to, so a blocked head does not stall unrelated work. The
/// skip is bounded: aging lifts the blocked head out of the skippers' band
/// within `AGING_SECONDS`, after which strict head-of-line applies.
pub fn next_job(
    queued: &[Pending],
    running: &[Pending],
    cursor: (&str, &str),
    now: f64,
) -> Option<Pending> {
    if queued.is_empty() {
        return None;
    }
    let mut ordered: Vec<&Pending> = queued.iter().collect();
    ordered.sort_by_key(|job| order_key(job, now, cursor));
    let head = ordered[0];
    if can_start(head, running) {
        return Some(head.clone());
    }
    let head_band = effective_rank(head, now).floor() as i64;
    for job in ordered.iter().skip(1) {
        if effective_rank(job, now).floor() as i64 != head_band {
            break;
        }
        if can_start(job, running) {
            return Some((*job).clone());
        }
    }
    None
}
