"""Data models for the WorkReady simulation API."""

from __future__ import annotations

from pydantic import BaseModel


# --- Resume assessment ---


class FeedbackDetail(BaseModel):
    """Structured feedback on a resume submission."""

    strengths: list[str]
    gaps: list[str]
    suggestions: list[str]
    tailoring: str


class AssessmentResult(BaseModel):
    """Resume assessment response."""

    status: str = "reviewed"
    application_id: int | None = None
    fit_score: int  # 0-100
    feedback: FeedbackDetail
    proceed_to_interview: bool
    message: str = ""


# --- Simulation state ---


class ApplicationSummary(BaseModel):
    """Summary of a student's application."""

    id: int
    company_slug: str
    job_slug: str
    job_title: str
    source: str
    current_stage: str
    current_interview_step: int = 0
    status: str = "active"  # active, rejected, hired, completed
    created_at: str
    updated_at: str


class StageResult(BaseModel):
    """Result of a single stage attempt."""

    id: int
    stage: str
    status: str
    score: int | None
    feedback: dict | None
    attempt: int
    created_at: str


class ApplicationDetail(BaseModel):
    """Full detail of an application including stage results."""

    application: ApplicationSummary
    stages: list[StageResult]


class StudentProgress(BaseModel):
    """All applications for a student."""

    email: str
    name: str
    applications: list[ApplicationSummary]


class BlockedJob(BaseModel):
    """A specific (company, job) blocked at the role level."""

    company_slug: str
    job_slug: str


class PublicPosting(BaseModel):
    """A posting as exposed to seek.jobs and other public consumers.

    Confidential postings have most company-revealing fields nulled out
    unless the requesting student has revealed the underlying company.
    """

    id: int
    source_type: str  # direct | agency
    agency_name: str | None = None
    listing_title: str
    listing_description: str | None = None
    confidential: bool = False
    # Company / job details — nulled when confidential and not revealed
    company_slug: str | None = None
    company_name: str | None = None
    company_url: str | None = None
    job_slug: str | None = None
    job_title: str | None = None
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    apply_url: str | None = None  # external link, null for confidential


class PostingList(BaseModel):
    """All postings for the job board."""

    postings: list[PublicPosting]
    total: int


# --- Interview (Stage 3) ---


class InterviewMessage(BaseModel):
    """A single message in an interview transcript."""

    role: str  # "assistant" (manager) or "user" (student)
    content: str


class InterviewSession(BaseModel):
    """An active interview session."""

    session_id: int
    application_id: int
    manager_name: str
    manager_role: str
    company_name: str
    job_title: str
    transcript: list[InterviewMessage]
    turn: int
    target_turns: int
    status: str  # "active" | "completed"
    feedback: dict | None = None
    final_score: int | None = None


class InterviewStartRequest(BaseModel):
    application_id: int


class InterviewMessageRequest(BaseModel):
    session_id: int
    message: str


class InterviewMessageReply(BaseModel):
    session_id: int
    reply: str
    turn: int
    target_turns: int
    suggested_wrap_up: bool


# --- Interview booking ---


class InterviewBooking(BaseModel):
    """An interview appointment booking."""

    id: int
    application_id: int
    scheduled_at: str  # UTC ISO
    status: str  # pending | completed | missed | cancelled
    created_at: str
    completed_at: str | None = None


class BookingState(BaseModel):
    """Current booking state for an application."""

    booking_enabled: bool
    application_id: int
    booking: InterviewBooking | None = None
    missed_count: int
    max_missed: int
    reschedule_count: int = 0
    max_reschedules: int = 0
    can_reschedule: bool = True
    can_book: bool
    rejection_imminent: bool = False  # one more miss = auto-reject


class SlotOption(BaseModel):
    """A single offered interview slot."""

    scheduled_at: str  # UTC ISO
    local_display: str  # human-readable in local timezone


class SlotOptions(BaseModel):
    """Slots offered for booking based on student preferences."""

    application_id: int
    slots: list[SlotOption]
    timezone: str
    business_hours: str  # e.g. "9am-5pm Mon-Fri"


class BookingRequest(BaseModel):
    scheduled_at: str  # UTC ISO


class StudentState(BaseModel):
    """High-level state for the portal — what the student should see."""

    email: str
    name: str
    state: str  # NOT_APPLIED, APPLIED, HIRED, COMPLETED
    active_application: ApplicationSummary | None = None
    applications: list[ApplicationSummary]
    unread_personal: int = 0
    unread_work: int = 0
    # Blocked companies (every role at this company is blocked)
    blocked_companies: list[str] = []
    # Blocked specific roles (only this job is blocked, not the whole company)
    blocked_jobs: list[BlockedJob] = []


class Message(BaseModel):
    """An inbox message."""

    id: int
    inbox: str
    sender_name: str
    sender_role: str | None = ""
    sender_email: str = "noreply@workready.eduserver.au"
    subject: str
    body: str
    application_id: int | None = None
    related_stage: str | None = None
    direction: str = "inbound"
    recipient_email: str | None = None
    thread_id: int | None = None
    status: str = "delivered"
    has_attachment: bool = False
    is_read: bool
    deliver_at: str
    created_at: str


class Inbox(BaseModel):
    """Inbox contents."""

    inbox: str
    messages: list[Message]
    unread_count: int


# --- Stage 4: Work tasks ---


class TaskFeedback(BaseModel):
    """Structured mentor feedback on a task submission."""

    strengths: list[str] = []
    improvements: list[str] = []
    summary: str = ""


class TaskSummary(BaseModel):
    """A task as shown to the student in their task list."""

    id: int
    sequence: int
    title: str
    brief: str
    difficulty: str
    status: str  # assigned | submitted | passed | failed | resubmit | under_review
    visible_at: str | None = None
    due_at: str | None = None
    submitted_at: str | None = None
    reviewed_at: str | None = None


class TaskDetail(BaseModel):
    """Full task detail including the description and latest submission."""

    id: int
    sequence: int
    title: str
    brief: str
    description: str
    difficulty: str
    status: str
    visible_at: str | None = None
    due_at: str | None = None
    submitted_at: str | None = None
    reviewed_at: str | None = None
    score: int | None = None
    feedback: TaskFeedback | None = None
    submission_body: str | None = None
    attachment_filename: str | None = None


class TaskList(BaseModel):
    """All visible tasks for an application."""

    application_id: int
    total: int
    tasks: list[TaskSummary]


class TaskSubmitResult(BaseModel):
    """Response after submitting a task.

    The outcome fields (score, status, feedback) may be null when the
    feedback delay has not yet elapsed — the student sees 'under_review'
    and the mentor's email lands later.
    """

    task_id: int
    status: str  # under_review | passed | failed | resubmit
    score: int | None = None
    feedback: TaskFeedback | None = None
    message: str = ""


# --- Stage 4c: Calendar ---


class CalendarEvent(BaseModel):
    """A single calendar event."""

    id: int
    event_type: str  # task_deadline | lunchroom | exit_interview | custom
    title: str
    description: str | None = None
    scheduled_at: str  # UTC ISO
    status: str  # upcoming | accepted | declined | completed | cancelled
    related_id: int | None = None
    created_at: str


class CalendarEventList(BaseModel):
    """All calendar events for an application."""

    application_id: int
    events: list[CalendarEvent]
    total: int


# --- Stage 5: Lunchroom ---


class LunchroomParticipant(BaseModel):
    """One AI character attending a lunchroom session."""

    slug: str
    name: str
    role: str = ""


class LunchroomSlot(BaseModel):
    """A proposed lunchtime slot the student can pick."""

    scheduled_at: str  # UTC ISO
    local_display: str  # human-readable in local timezone


class LunchroomSession(BaseModel):
    """A lunchroom session — invitation, accepted, or completed.

    Covers the full lifecycle: when status='invited' there's no
    scheduled_at yet and the proposed_slots carry the options; once
    accepted, scheduled_at is set and proposed_slots is empty.
    """

    id: int
    application_id: int
    occasion: str  # routine_lunch | task_celebration | birthday | ...
    occasion_detail: str | None = None
    participants: list[LunchroomParticipant] = []
    proposed_slots: list[LunchroomSlot] = []
    scheduled_at: str | None = None
    status: str  # invited | accepted | active | completed | declined | missed | cancelled
    trigger_source: str | None = None
    invitation_message_id: int | None = None
    calendar_event_id: int | None = None
    created_at: str
    completed_at: str | None = None


class LunchroomSessionList(BaseModel):
    """All lunchroom sessions for an application, newest first."""

    application_id: int
    sessions: list[LunchroomSession]
    total: int


class LunchroomSlotPickRequest(BaseModel):
    """Request body for picking a proposed slot."""

    scheduled_at: str  # must match one of the proposed slot ISO strings


# --- Stage 5b: lunchroom chat ---


class LunchroomPost(BaseModel):
    """A single delivered post in the lunchroom group chat."""

    id: int
    session_id: int
    sequence: int
    author_kind: str  # student | character | system
    author_slug: str | None = None
    author_name: str | None = None
    content: str | None = None
    deliver_at: str
    status: str  # pending | delivered
    mentions: list[str] = []


class LunchroomChatState(BaseModel):
    """Portal poll response — session status plus all visible posts."""

    session_id: int
    status: str  # accepted | active | completed
    soft_cap: int
    hard_cap: int
    delivered_count: int
    posts: list[LunchroomPost] = []


class LunchroomPostRequest(BaseModel):
    """Request body for a student posting to the chat."""

    content: str


# --- Stage 7: Team directory ---


class TeamMemberRef(BaseModel):
    """Team or org member reference returned by /api/v1/team/{application_id}."""

    slug: str
    name: str
    role: str = ""
    email: str = ""
    presence_ok: bool = False
    availability_status: str = "available"
    availability_note: str = ""
    email_only: bool = False


class TeamBusinessHours(BaseModel):
    """Company business hours used by the chat/email availability logic."""

    start: int = 9
    end: int = 17
    days: list[int] = []
    holidays_region: str | None = None


class TeamDirectoryResponse(BaseModel):
    """Team directory payload: immediate team, wider org, and business hours."""

    team: list[TeamMemberRef] = []
    org: list[TeamMemberRef] = []
    business_hours: TeamBusinessHours = TeamBusinessHours()


# --- Stage 7: Chat routes ---


class ChatSendRequest(BaseModel):
    application_id: int
    character_slug: str
    content: str


class ChatMessageModel(BaseModel):
    id: int
    channel: str
    author: str  # "student" | "character"
    sender_name: str
    content: str
    created_at: str
    deliver_at: str | None = None


class ChatThreadResponse(BaseModel):
    application_id: int
    character_slug: str
    character_name: str
    character_role: str = ""
    presence_ok: bool = False
    messages: list[ChatMessageModel] = []
