from prometheus.config import Settings, data_dir, get_secret, load_settings, save_settings


def test_data_dir_uses_env_override(tmp_home):
    assert data_dir() == tmp_home.data
    assert tmp_home.data.is_dir()


def test_settings_roundtrip(tmp_home):
    defaults = load_settings()
    assert defaults.host == "127.0.0.1"
    assert defaults.budget_usd == 5.0
    assert defaults.routes["private"] == ["ollama"]
    changed = Settings(budget_usd=2.5, allow_lan=True)
    save_settings(changed)
    assert load_settings() == changed


def test_get_secret_app_saved_key_beats_environment(tmp_home, monkeypatch):
    (data_dir() / ".env").write_text("X=file\n# comment\nY = spaced \n", encoding="utf-8")
    monkeypatch.setenv("X", "env")
    monkeypatch.setenv("Z", "only-env")
    assert get_secret("X") == "file"      # a key saved in the app wins over setx
    assert get_secret("Z") == "only-env"
    assert get_secret("Y") == "spaced"
    assert get_secret("UNKNOWN_SECRET_NAME") is None
