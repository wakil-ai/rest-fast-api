# Milvus Filter Generation Prompt with Multi-Source Analysis

## Introduction
This prompt is designed to guide the generation of Milvus filter expressions for querying a vector database containing judicial documents. The filter generation analyzes THREE sources of information:
1. **User Query**: Direct user request or question
2. **Uploaded File Content**: Document content uploaded by user (if available)
3. **Chat History**: Previous conversation context (if available)

The goal is to create accurate, syntactically correct Milvus filter strings by intelligently combining information from all three sources. The priority order is: Uploaded File > User Query > Chat History.

## Data Structure
The Milvus collection contains the following filterable fields:

### 1. Metadata Field (JSON)
The metadata for documents is structured as a JSON field named 'metadata' with the following keys:

- **court_names_uz**: This is a string key in the metadata JSON representing the name of the court in Uzbek Cyrillic. It is the primary filtering attribute and should be applied first in filter generation to scope queries to specific courts. The key contains one of the following unique values:

{court_names_uz_values}

Note: Please use exactly the name mentioned on the list in *court_names_uz* without changing to cyrilic or any other format.

- **instance**: This is a string key in the metadata JSON representing the judicial instance level. It is the next key filtering attribute after `court_names_uz`. The key contains one of the following unique values (stored in English):
  - FIRST
  - INSPECTION
  - REPEATED_INSPECTION
  - APPEAL
  - CASSATION
  - CASSATION_REPEATED
  - PRESIDIUM

  User queries may reference these instances in Uzbek. Map detected Uzbek terms to their corresponding English values:
  - "Birinchi instansiya" → "FIRST"
  - "Taftish instansiyasi" → "INSPECTION"
  - "Takroriy taftish instansiyasi" → "REPEATED_INSPECTION"
  - "Apellyatsiya instansiyasi" → "APPEAL"
  - "Kassatsiya instansiyasi" → "CASSATION"
  - "Takroriy kassatsiya instansiyasi" → "CASSATION_REPEATED"
  - "Rayosat" → "PRESIDIUM"

- **categories_uz**: This is a string key in the metadata JSON representing the category of the judicial matter in Uzbek Cyrillic. The key contains one of the following unique values:

{categories_uz_values}

Note: Please use exactly the name mentioned on the list in *categories_uz* without changing to cyrilic or any other format.

### 2. Text Field (String)
The 'text' field contains the full document content in Uzbek Cyrillic. This field should be used for content-based filtering when users search for specific keywords, topics, or phrases within the documents.

## Multi-Source Analysis Strategy

### Priority Order:
1. **Uploaded File (HIGHEST PRIORITY)**: If a file is uploaded, extract court name, instance, category, and key topics from the file content. This takes precedence as the user wants to find similar documents.
2. **User Query (MEDIUM PRIORITY)**: Parse the explicit user request for filtering criteria.
3. **Chat History (LOWEST PRIORITY)**: Use conversation context to fill in missing information or understand user intent.

### Analysis Guidelines:

#### A. Uploaded File Analysis:
When uploaded file content is provided:
- **Extract Court Information**: Search for court names in the file (e.g., "Тошкент шаҳар маъмурий суди", "Ўзбекистон Республикаси Олий суди"). Match exactly to the court_names_uz values.
- **Extract Instance Information**: Look for instance markers like "Биринчи инстанция", "Апелляция", "Кассация", "Райосат" and map to English values.
- **Extract Category Information**: Identify the case category from the document content and match to categories_uz values.
- **Extract Key Topics**: Identify important legal terms, concepts, or keywords that should be searched in the text field (convert to Cyrillic).
- **Goal**: The filter should help find documents SIMILAR to the uploaded file (same court, same instance, same category, or related topics).

#### B. User Query Analysis:
- Parse explicit filtering requests (e.g., "Toshkent shahar sudida", "soliq haqida")
- Extract court names, instances, categories, and keywords
- If the user says "shunga o'xshash" (similar to this), "bunday hujjatlar" (such documents), or references "uploaded file", prioritize the uploaded file analysis

#### C. Chat History Analysis:
- Review previous messages for context about:
  - Previously mentioned courts, instances, or categories
  - Ongoing research topics or keywords
  - User preferences or filtering patterns
- Use this to supplement missing information but do NOT override explicit user query or uploaded file data

### Combining Information:
- If uploaded file specifies "Тошкент шаҳар маъмурий суди" and user query says "soliq haqida", combine both: `metadata["court_names_uz"] == "Тошкент шаҳар маъмурий суди" && text like "%солиқ%"`
- If user query explicitly contradicts uploaded file, prefer USER QUERY
- If chat history mentions a court but uploaded file specifies different court, prefer UPLOADED FILE
- If no court in uploaded file or user query, check chat history

## Filter Generation Guidelines

1. **Syntax**: Use Milvus filter expression syntax:
   - For metadata JSON fields: `metadata["field"] == "value"`, `metadata["field"] in ["value1", "value2"]`
   - For text field: `text like "%keyword%"` (case-sensitive pattern matching)
   - Logical operators: `&&` (and), `||` (or), `!=` (not equal)
   - Ensure strings are quoted properly with double quotes

2. **Sequence of Filters**: 
   - Start with `metadata["court_names_uz"]` to scope by court (if identified from any source)
   - Next, apply `metadata["instance"]` to filter by judicial level (if identified)
   - Then, apply `metadata["categories_uz"]` to filter by category (if identified)
   - Finally, apply `text like "%keyword%"` for content-based filtering (if identified)
   - Combine them using `&&` for intersection

3. **Text Filtering Rules**:
   - **CRITICAL**: ALL text search keywords MUST be converted to Uzbek Cyrillic script
   - When user mentions keywords in Latin script (e.g., "soliq", "davlat", "fuqaro"), convert them to Cyrillic (е.g., "солиқ", "давлат", "фуқаро")
   - Use the `like` operator with `%` wildcards: `text like "%keyword%"`
   - Multiple keywords should be combined with `&&` or `||` as appropriate:
     - `&&` when all keywords must be present: `text like "%солиқ%" && text like "%давлат%"`
     - `||` when any keyword can match: `text like "%солиқ%" || text like "%пошлина%"`
   - Preserve exact Cyrillic spelling and case

4. **Keyword Translation Examples** (Latin → Cyrillic):
   - soliq → солиқ
   - davlat → давлат
   - fuqaro → фуқаро
   - sud → суд


5. **Output Format**: Provide the filter as a single string without any additional text or explanations. The output must be directly executable in Milvus and contain no other content.

## Example Usage

**Scenario 1: Uploaded File + User Query**
- Uploaded File Content: "...Тошкент шаҳар маъмурий суди...Биринчи инстанция...солиқ тўловлари..."
- User Query: "shunga o'xshash hujjatlar"
- Chat History: (empty)
**Generated Filter**: `metadata["court_names_uz"] == "Тошкент шаҳар маъмурий суди" && metadata["instance"] == "FIRST" && text like "%солиқ%"`

**Scenario 2: No Uploaded File + User Query**
- Uploaded File Content: (none)
- User Query: "O'zbekiston Oliy sudida davlat organlari haqida"
- Chat History: (empty)
**Generated Filter**: `metadata["court_names_uz"] == "Ўзбекистон Республикаси Олий суди" && text like "%давлат%" && text like "%орган%"`

**Scenario 3: Uploaded File + User Query Override**
- Uploaded File Content: "...Бухоро вилоят маъмурий суди..."
- User Query: "Yo'q, Farg'ona viloyatida qidiring"
- Chat History: (empty)
**Generated Filter**: `metadata["court_names_uz"] == "Фарғона вилоят маъмурий суди"`

**Scenario 4: Chat History Context**
- Uploaded File Content: (none)
- User Query: "yana shu mavzu bo'yicha"
- Chat History: "User: Samarqand viloyatida litsenziya haqida hujjatlar kerak..."
**Generated Filter**: `metadata["court_names_uz"] == "Самарқанд вилоят маъмурий суди" && text like "%лицензия%"`

**Scenario 5: All Three Sources**
- Uploaded File Content: "...Тошкент шаҳар маъмурий суди...Апелляция инстанцияси...мулк ҳуқуқлари..."
- User Query: "bunday ishlarni va soliq haqida ham"
- Chat History: "User: Davlat organlari bilan bog'liq ishlar..."
**Generated Filter**: `metadata["court_names_uz"] == "Тошкент шаҳар маъмурий суди" && metadata["instance"] == "APPEAL" && (text like "%мулк%" || text like "%солиқ%")`

**Scenario 6: Uploaded File with Multiple Topics**
- Uploaded File Content: "...Ўзбекистон Республикаси Олий суди...Кассация...нотариал ҳаракатлар...фуқаро ҳуқуқлари..."
- User Query: "find similar cases"
- Chat History: (empty)
**Generated Filter**: `metadata["court_names_uz"] == "Ўзбекистон Республикаси Олий суди" && metadata["instance"] == "CASSATION" && (text like "%нотариал%" || text like "%фуқаро%")`

---

Now, analyze the following inputs and generate a Milvus filter:

**User Query**: {user_query}

**Uploaded file context**: {file_context}

**Chat History**: {chat_history}

Generate ONLY the filter string, nothing else.

Filter:
