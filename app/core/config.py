from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 30
    # AI provider — prefix determines provider:
    #   claude-*  → Anthropic   (needs anthropic_api_key)
    #   gemini-*  → Google      (needs google_api_key, free tier available)
    #   llama-* / mixtral-* / etc. → Groq (needs groq_api_key, free tier available)
    ai_model: str = "gemini-2.0-flash"
    anthropic_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_exam_price_id: str = ""
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    frontend_url: str = "http://localhost:3000"
    bypass_subscription: bool = False  # Set to True in .env to skip payment gates during testing

    class Config:
        env_file = ".env"


settings = Settings()
