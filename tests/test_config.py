"""Конфиг и секреты: .env читается и пишется, секреты переезжают из settings.json, значения не теряются.
Работает на временных файлах с подставными значениями — настоящие settings.json и .env не трогает."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="gp_cfg_"))
st, env = tmp / "settings.json", tmp / ".env"
st.write_text(json.dumps({"gemini_api_key": "FAKE-KEY-123", "tg_bot_token": "", "phone_token": "FAKE-PHONE", "ai_daily_limit": 50,
                          "phone_on": True}), encoding="utf-8")
moved = config.migrate_secrets(st, env)
assert sorted(moved) == ["GEMINI_API_KEY", "PHONE_TOKEN"], moved
s = json.loads(st.read_text(encoding="utf-8"))
assert s["gemini_api_key"] == "" and s["phone_token"] == "" and s["ai_daily_limit"] == 50 and s["phone_on"] is True, s
assert config.read_env(env) == {"GEMINI_API_KEY": "FAKE-KEY-123", "PHONE_TOKEN": "FAKE-PHONE"}
assert config.migrate_secrets(st, env) == []                                    # второй раз переносить нечего
over = config.secrets_overlay(s, env)
assert over["gemini_api_key"] == "FAKE-KEY-123" and over["tg_bot_token"] == "" and over["ai_daily_limit"] == 50
# значение в .env главнее того, что снова вписали в settings.json
st.write_text(json.dumps({"gemini_api_key": "OTHER"}), encoding="utf-8")
config.migrate_secrets(st, env)
assert config.read_env(env)["GEMINI_API_KEY"] == "FAKE-KEY-123"
# запись: комментарии и чужие строки целы, пустое значение стирает секрет
env.write_text("# комментарий\nFOO=bar\nGEMINI_API_KEY=old\n", encoding="utf-8")
config.save_secrets({"gemini_api_key": "new", "phone_token": "", "not_secret": "x"}, env)
assert env.read_text(encoding="utf-8").splitlines() == ["# комментарий", "FOO=bar", "GEMINI_API_KEY=new", "PHONE_TOKEN="]
assert config.secrets_overlay({"phone_token": ""}, env)["phone_token"] == ""
# конфиг: значения по умолчанию, чужие ключи и неверные типы не берутся
c = config.load_config()
assert c["watch_gap"] >= 1 and 0 <= c["jitter"] <= 2 and c["log_dir"]
print("конфиг и секреты: тесты пройдены")
