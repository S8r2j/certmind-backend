from pydantic import BaseModel, Field
from typing import Optional


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    middle_name: Optional[str] = Field(None, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None       # ISO date string: YYYY-MM-DD
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


class ProfileResponse(BaseModel):
    email: str
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    employment_details: Optional[str] = None
    goals: Optional[str] = None


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


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class ResendVerificationRequest(BaseModel):
    email: str


class QuestionRequest(BaseModel):
    exam_slug: str


class AnswerRequest(BaseModel):
    exam_slug: str
    question_id: str
    answer: str = Field(..., min_length=1, max_length=1)


class ChatRequest(BaseModel):
    exam_slug: str
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None  # None = start new session


class CheckoutRequest(BaseModel):
    exam_slug: str


class ProgressResponse(BaseModel):
    exam_slug: str
    total_answered: int
    total_correct: int
    domain_scores: dict


class SubscriptionResponse(BaseModel):
    active: bool
    exam_slug: Optional[str] = None
    expires_at: Optional[str] = None
    days_remaining: Optional[int] = None
    is_trial: Optional[bool] = False
