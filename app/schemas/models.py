from pydantic import BaseModel, Field
from typing import Optional, List


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    employment_details: Optional[str] = None
    goals: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    session_token: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class ResendVerificationRequest(BaseModel):
    email: str


class ProfileResponse(BaseModel):
    email: str
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    employment_details: Optional[str] = None
    goals: Optional[str] = None
    is_admin: bool = False


class UpdateProfileRequest(BaseModel):
    first_name: Optional[str] = Field(None, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    employment_details: Optional[str] = None
    goals: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class QuestionRequest(BaseModel):
    exam_slug: str
    tab_id: str = ""


class AnswerRequest(BaseModel):
    exam_slug: str
    question_id: str
    answer: str = Field(..., min_length=1, max_length=20)  # "A" or "A,C" for multi-select
    time_spent_seconds: Optional[int] = None


class ChatRequest(BaseModel):
    exam_slug: str
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None


class CheckoutRequest(BaseModel):
    exam_slug: str
    coupon_code: Optional[str] = None


class ProgressResponse(BaseModel):
    exam_slug: str
    total_answered: int
    total_correct: int
    domain_scores: dict
    streak_days: int = 0
    time_committed_seconds: int = 0


class AttemptItem(BaseModel):
    id: str
    question_id: str
    stem: str
    options: List[dict]
    correct_answer: str
    user_answer: str
    option_explanations: dict
    is_correct: bool
    attempted_at: str


class AttemptsResponse(BaseModel):
    attempts: List[AttemptItem]
    total: int
    page: int
    page_size: int


class SubscriptionResponse(BaseModel):
    active: bool
    exam_slug: Optional[str] = None
    expires_at: Optional[str] = None
    days_remaining: Optional[int] = None
    is_trial: Optional[bool] = False
    trial_question_limit: Optional[int] = None


class CouponResponse(BaseModel):
    id: str
    code: str
    discount_pct: int
    max_uses: Optional[int] = None
    used_count: int
    expires_at: Optional[str] = None
    is_active: bool
    stripe_coupon_id: Optional[str] = None


class CreateCouponRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    discount_pct: int = Field(..., ge=1, le=100)
    max_uses: Optional[int] = None
    expires_at: Optional[str] = None


class CreateCourseRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=100)
    title: str = Field(..., min_length=2, max_length=200)
    code: str = Field(..., min_length=2, max_length=20)
    description: Optional[str] = None
    domains: List[dict] = []   # [{"name": "...", "weight": 0.25}, ...]


class ExtendTrialRequest(BaseModel):
    days: int = Field(..., ge=1, le=90)
    exam_slug: str = ""  # required only when no trial exists yet for the user


class GrantAccessRequest(BaseModel):
    exam_slug: str = Field(..., min_length=1)
    days: int = Field(default=365, ge=1, le=3650)  # default 1 year
