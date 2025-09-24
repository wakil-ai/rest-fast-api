import fasttext
from enum import Enum
from UzTransliterator import UzTransliterator


class LanguageConfigs(Enum):
    UZBEK_CYRILLIC = (
        "__label__uz_cyr", "Uzbek Cyrillic", "uz",
        "INSTRUCTION: The assistant MUST reply strictly in the Uzbek Cyrillic script. "
        "Do NOT use any Latin characters or mix alphabets.",
    )
    UZBEK_LATIN = (
        "__label__uz_lat", "Uzbek Latin", "uz",
        "INSTRUCTION: The assistant MUST reply strictly in the Uzbek Latin script. "
        "Do NOT use any Cyrillic characters or mix alphabets.",
    )
    RUSSIAN = (
        "__label__rus", "Russian", "ru",
        "INSTRUCTION: The assistant MUST reply strictly in the Russian language. "
        "Do NOT use any other languages or mix alphabets.",
    )
    ENGLISH = (
        "__label__eng", "English", "en",
        "INSTRUCTION: The assistant MUST reply strictly in the English language. "
        "Do NOT use any Cyrillic characters or mix alphabets.",
    )
    OTHER = (
        "__label__other", "Other", "",
        "INSTRUCTION: The assistant MUST respond in language that user gave.",
    )

    @property
    def label(self): return self.value[0]
    @property
    def display(self): return self.value[1]
    @property
    def code(self): return self.value[2]
    @property
    def instruction(self): return self.value[3]
    @property
    def filter_expression(self): return self.value[4]


class LanguageDetector:
    def __init__(self):
        self.model = fasttext.load_model("app/assets/langdetect.ftz")
        self.label_map = {lang.label: lang for lang in LanguageConfigs if lang != LanguageConfigs.OTHER}
        self.display_map = {lang.display: lang for lang in LanguageConfigs}
        self.converter = UzTransliterator.UzTransliterator()
    
    def detect_language(self, text: str) -> str:
        try:
            label, _ = self.model.predict(text)
            lang = self.label_map.get(label[0], LanguageConfigs.OTHER)
            return lang.display
        except Exception as e:
            return LanguageConfigs.OTHER.display

    def get_instruction(self, language: str) -> str:
        try:
            lang = self.display_map.get(language, LanguageConfigs.OTHER)
            return lang.instruction
        except Exception as e:
            return LanguageConfigs.OTHER.instruction