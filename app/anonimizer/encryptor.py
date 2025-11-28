import re
import json
from typing import Dict, List
from dataclasses import dataclass

@dataclass
class PIIEntity:
    text: str
    entity_type: str
    start: int
    end: int
    token: str


class UzbekRussianPIIDetector:
    """PII detector for Uzbek and Russian documents"""
    
    def __init__(self):
        self.patterns = {
            'STIR': r'\b(?:СТИР|STIR)\s*:?\s*\d{9}\b|\b\d{9}\b',
            'JSHSHIR': r'\b(?:ЖШШИР|JSHSHIR|ПИНФЛ|PINFL)\s*:?\s*\d{14}\b|\b\d{14}\b',
            'PASSPORT_UZ': r'\b(?:паспорт|passport)\s*:?\s*[A-Z]{2}\d{7}\b|\b[A-Z]{2}\d{7}\b',
            'PASSPORT_RU': r'\b(?:паспорт|passport)\s*:?\s*\d{4}\s?\d{6}\b',
            'PHONE_UZ': r'(?:\+998|998)[\s\-]?(?:\d{2})[\s\-]?(?:\d{3})[\s\-]?(?:\d{2})[\s\-]?(?:\d{2})',
            'PHONE_RU': r'(?:\+7|8|7)[\s\-]?(?:\(?\d{3}\)?)[\s\-]?(?:\d{3})[\s\-]?(?:\d{2})[\s\-]?(?:\d{2})',
            'EMAIL': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b',
            'BANK_ACCOUNT': r'\b(?:р/с|р\.с|счет|account|hisob)\s*:?\s*\d{20}\b|\b\d{20}\b',
            'MFO': r'\b(?:МФО|MFO)\s*:?\s*\d{5}\b',
            'INN': r'\b(?:ИНН|INN)\s*:?\s*\d{10,12}\b',
            'CARD_NUMBER': r'\b(?:\d{4}[\s\-]?){3}\d{4}\b',
            'SNILS': r'\b(?:СНИЛС|SNILS)\s*:?\s*\d{3}-\d{3}-\d{3}\s\d{2}\b|\b\d{3}-\d{3}-\d{3}\s\d{2}\b',
            'CONTRACT_NUM': r'(?:№|#|N|договор|контракт|shartnoma|contract)\s*\.?\s*[A-ZА-ЯЎҚҒҲa-zа-яўқғҳ\d]+-?\d+(?:[-/]\d+)*',
            'CADASTRAL': r'\b\d{2}:\d{2}:\d{2,}:\d{2,}:\d{2,}:\d{4,}\b',
            'DATE': r'\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b',
        }
        self.token_map: Dict[str, str] = {}
        self.reverse_map: Dict[str, str] = {}
        self.counter: Dict[str, int] = {}
        self.detected_entities: List[PIIEntity] = []
    
    def _detect_names(self, text: str) -> List:
        """Detect person and organization names in Cyrillic and Latin."""

        entities = []

        name_patterns = [
            # 1) Cyrillic full name with patronymic
            #    e.g. "Иванов Иван Иванович"
            (
                r'\b[А-ЯЁЎҚҒҲ][а-яёўқғҳ]{2,}\s+'
                r'[А-ЯЁЎҚҒҲ][а-яёўқғҳ]{2,}\s+'
                r'[А-ЯЁЎҚҒҲ][а-яёўқғҳ]+(?:ович|евич|овна|евна|оглы|кызы)\b',
                'PERSON'
            ),

            # 2) Cyrillic: Firstname Lastname
            #    e.g. "Иван Петров"
            (
                r'\b[А-ЯЁЎҚҒҲ][а-яёўқғҳ]{2,}\s+'
                r'[А-ЯЁЎҚҒҲ][а-яёўқғҳ]{2,}\b',
                'PERSON'
            ),

            # 3) Cyrillic initial + surname (+ optional "нинг"/"ning")
            #    e.g. "Н.Хидиров", "Н. Хидиров", "Н.Хидировнинг"
            (
                r'\b[А-ЯЁЎҚҒҲ]\.\s*[А-ЯЁЎҚҒҲ][а-яёўқғҳ]{2,}(?:нинг|ning)?\b',
                'PERSON'
            ),

            # 4) Latin: Firstname Lastname (+ optional middle)
            #    e.g. "Nodir Xidirov", "Aziza Karimova", "Ali Valiyev Karimov"
            (
                r'\b[A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?\b',
                'PERSON'
            ),

            # 5) Latin initial + surname (+ optional "ning"/"нинг")
            #    e.g. "N.Xidirov", "N. Xidirovning"
            (
                r'\b[A-Z]\.\s*[A-Z][a-z]{2,}(?:ning|нинг)?\b',
                'PERSON'
            ),

            # 6) Organization: type FIRST (Cyrillic or Latin)
            #    e.g. "МЧЖ \"BENTOP PRODUCTS\"", "OOO \"ОЛТИН ВОДИЙ\"",
            #         "MCHJ ABC GROUP", "ABC LLC", "XYZ JSC"
            (
                r'\b(?:ООО|ОАО|ЗАО|АО|ИП|ТОО|МЧЖ|QMJ|MCHJ|LLC|JSC)\s+'
                r'[«"“”]?[A-ZА-ЯЁЎҚҒҲ][A-ZА-ЯЁЎҚҒҲa-zа-яёўқғҳ0-9\s\-&]+[»"“”]?\b',
                'ORGANIZATION'
            ),

            # 7) Organization: NAME first, type AFTER (+ optional "нинг"/"ning")
            #    e.g. "“BENTOP PRODUCTS” МЧЖнинг", "ABC GROUP LLC", "XYZ MCHJ"
            (
                r'[«"“”]?[A-ZА-ЯЁЎҚҒҲ][A-ZА-ЯЁЎҚҒҲa-zа-яёўқғҳ0-9\s\-&]+[»"“”]?\s+'
                r'(?:ООО|ОАО|ЗАО|АО|ИП|ТОО|МЧЖ|QMJ|MCHJ|LLC|JSC)(?:нинг|ning)?\b',
                'ORGANIZATION'
            ),
        ]

        for pattern, entity_type in name_patterns:
            for match in re.finditer(pattern, text):
                # strip straight + smart quotes so token doesn't include them
                name = match.group().strip('"“”«»')
                if len(name) > 4:
                    entities.append((name, match.start(), match.end(), entity_type))

        return entities

    
    def _validate_entity(self, entity_type: str, value: str) -> bool:
        """Validate detected entity"""
        clean_value = re.sub(r'\D', '', value)
        validators = {
            'JSHSHIR': lambda v: len(v) == 14,
            'STIR': lambda v: len(v) == 9,
            'BANK_ACCOUNT': lambda v: len(v) == 20,
            'INN': lambda v: len(v) in [10, 12],
            'MFO': lambda v: len(v) == 5,
        }
        validator = validators.get(entity_type)
        return validator(clean_value) if validator else True
    
    def _create_token(self, entity_type: str, original_value: str) -> str:
        """Create unique token"""
        if original_value in self.reverse_map:
            return self.reverse_map[original_value]
        
        count = self.counter.get(entity_type, 0) + 1
        self.counter[entity_type] = count
        token = f"[{entity_type}_{count}]"
        
        self.token_map[token] = original_value
        self.reverse_map[original_value] = token
        
        return token
    
    def mask_text(self, text: str) -> str:
        """Mask all PII in text"""
        self.detected_entities = []
        entities_to_mask = []
        
        # Pattern-based detection
        for entity_type, pattern in self.patterns.items():
            for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
                original_value = match.group().strip()
                if self._validate_entity(entity_type, original_value):
                    entities_to_mask.append({
                        'text': original_value,
                        'type': entity_type,
                        'start': match.start(),
                        'end': match.end()
                    })
        
        # Name detection
        name_entities = self._detect_names(text)
        for name, start, end, entity_type in name_entities:
            entities_to_mask.append({
                'text': name,
                'type': entity_type,
                'start': start,
                'end': end
            })
        
        # Remove overlaps
        entities_to_mask.sort(key=lambda x: (x['start'], -(x['end'] - x['start'])))
        filtered = []
        last_end = -1
        for entity in entities_to_mask:
            if entity['start'] >= last_end:
                filtered.append(entity)
                last_end = entity['end']
        
        # Sort by position (reverse)
        filtered.sort(key=lambda x: x['start'], reverse=True)
        
        # Replace with tokens
        masked_text = text
        for entity in filtered:
            token = self._create_token(entity['type'], entity['text'])
            
            pii_entity = PIIEntity(
                text=entity['text'],
                entity_type=entity['type'],
                start=entity['start'],
                end=entity['end'],
                token=token
            )
            self.detected_entities.append(pii_entity)
            
            masked_text = (
                masked_text[:entity['start']] +
                token +
                masked_text[entity['end']:]
            )
        
        return masked_text
    
    def unmask_text(self, masked_text: str) -> str:
        """Restore original PII from tokens"""
        unmasked_text = masked_text
        for token, original_value in self.token_map.items():
            unmasked_text = unmasked_text.replace(token, original_value)
        return unmasked_text
    
    def save_mapping(self, filepath: str):
        """Save token mappings to file"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.token_map, f, ensure_ascii=False, indent=2)
    
    def load_mapping(self, filepath: str):
        """Load token mappings from file"""
        with open(filepath, 'r', encoding='utf-8') as f:
            self.token_map = json.load(f)
        self.reverse_map = {v: k for k, v in self.token_map.items()}

