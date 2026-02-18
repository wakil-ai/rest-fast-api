from collections.abc import Iterable
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    WeightedRanker,
)

from app.core.config import settings
from app.core.logger import logger
from app.db.vector_db_handler import VectorDBHandler
from app.utils.text_cleaning import extract_integers


class MilvusHandler(VectorDBHandler):
    def __init__(self):
        # Connect to Milvus given URI
        self.milvus_collections = [
            settings.MILVUS_MAIN_NAME,
            settings.MILVUS_SOLIQ_ASSISTANT_NAME,
            settings.MILVUS_PROJECT_FILES,
            settings.MILVUS_MAMURIY_SUD,
            settings.MILVUS_MAMURIY_SUD_ALL,
            settings.MILVUS_SHARTNOMA,
        ]
        self.client = MilvusClient(
            uri=settings.MILVUS_URI,
            user=settings.MILVUS_USER,
            password=settings.MILVUS_PASSWORD,
        )

        # Create and load all collections at startup
        for col in self.milvus_collections:
            self.create_collection(col)

        # Ensure all collections are loaded into memory at startup
        self._load_all_collections()

    def _load_all_collections(self) -> None:
        """
        Load all collections into memory at startup to avoid latency during queries.
        """
        for collection_name in self.milvus_collections:
            try:
                if self.client.has_collection(collection_name):
                    self.client.load_collection(collection_name)
            except Exception as e:
                logger.error(f"Failed to load collection '{collection_name}': {e}")

    def create_collection(self, collection_name: str):
        # Drop existing collection if it exists
        if self.client.has_collection(collection_name):
            return collection_name

        # Create collection with schema
        self.client.create_collection(
            collection_name=collection_name, schema=self._create_schema()
        )

        # Create index
        self.client.create_index(
            collection_name=collection_name, index_params=self._create_index()
        )

        # Load collection into memory
        self.client.load_collection(collection_name)
        logger.info(f"Created and loaded collection {collection_name}")

        return collection_name

    def create_partition(
        self, partition_name: str, collection_name: str = settings.MILVUS_MAIN_NAME
    ) -> str:
        # Create if missing
        if not self.client.has_partition(collection_name, partition_name):
            self.client.create_partition(
                collection_name=collection_name,
                partition_name=partition_name,
            )
            logger.info(f"Created partition '{partition_name}' in {collection_name}")
        else:
            return partition_name

    def upsert_vectors(
        self,
        documents: list[dict[str, Any]],
        partition_name: str = None,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> None:
        milvus_data = []

        if partition_name and not self.client.has_partition(
            collection_name=collection_name, partition_name=partition_name
        ):
            partition_name = self.create_partition(partition_name)

        for doc in documents:
            if "id" not in doc or "embedding" not in doc:
                logger.warning(f"Document missing required fields: {doc.keys()}")
                continue

            # Get text from metadata or direct text field
            text = doc.get("metadata", {}).get("text", "") or doc.get("text", "")
            if not text:
                logger.warning(f"Missing text for document ID: {doc.get('id')}")
                continue

            # Get hierarchy path from metadata
            hierarchy_path = doc.get("metadata", {}).get("hierarchy_path", "")

            entry = {
                "id": doc["id"],
                "text": text,
                "text_dense": doc["embedding"],
                "hierarchy_path": hierarchy_path,
                "metadata": doc.get("metadata", {}),
            }
            milvus_data.append(entry)

        if milvus_data:
            try:
                self.client.insert(
                    collection_name, milvus_data, partition_name=partition_name
                )
            except Exception as e:
                logger.error(f"Error upserting to Milvus: {str(e)}")
                raise

    def query(
        self,
        filter: str = str,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """
        Perform direct search using filter
        """
        results = self.client.query(
            collection_name=collection_name,
            filter=filter,
            output_fields=["text", "metadata"],
        )

        format_results = []
        for res in results:
            format_results.append(
                {"metadata": res.get("metadata", {}), "text": res.get("text", "")}
            )

        return format_results

    def query_hybrid(
        self,
        dense_vector: list[float],
        text_query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        partitions: list[str] = None,
        expr: str = None,
    ) -> list[dict[str, Any]]:
        """
        Perform hybrid search using both dense vectors and BM25 sparse vectors
        """
        logger.debug(
            f"TOP_K: {top_k}, ALPHA: {alpha} with collection: {collection_name}, filter: {expr}"
        )

        # Create search requests for both dense and sparse vectors
        dense_search = AnnSearchRequest(
            data=[dense_vector],
            anns_field="text_dense",
            param={"metric_type": "COSINE"},
            limit=top_k,
            expr=expr,
        )

        sparse_search = AnnSearchRequest(
            data=[text_query],
            anns_field="text_sparse",
            param={"drop_ratio_search": 0.2},
            limit=top_k,
            expr=expr,
        )

        # Perform hybrid search with weighted ranking - pass weights as separate arguments
        ranker = WeightedRanker(alpha, 1 - alpha)
        results = self.client.hybrid_search(
            collection_name=collection_name,
            reqs=[dense_search, sparse_search],
            ranker=ranker,
            limit=top_k,
            output_fields=["text", "metadata"],
            partitions=partitions,
        )

        return self._parse_results(results)

    # Dense search
    def query_dense(
        self,
        dense_vector: list[float],
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        partitions: list[str] = None,
        expr: str = None,
    ) -> list[dict[str, Any]]:
        """
        Perform dense vector search using semantic similarity
        """
        results = self.client.search(
            collection_name=collection_name,
            data=[dense_vector],
            anns_field="text_dense",
            search_params={"metric_type": "COSINE"},
            limit=top_k,
            output_fields=["text", "metadata"],
            partitions=partitions,
            filter=expr if expr else None,
        )

        return self._parse_results(results)

    # Sparse search
    def query_sparse(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        anns_field: str = "text_sparse",
        collection_name: str = settings.MILVUS_MAIN_NAME,
        partitions: list[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Perform BM25 sparse vector search using keyword matching
        """
        results = self.client.search(
            collection_name=collection_name,
            data=[text_query],
            anns_field=anns_field,
            search_params={"drop_ratio_search": 0.2},
            limit=top_k,
            output_fields=["text", "metadata"],
            partitions=partitions,
        )

        return self._parse_results(results)

    # Specific search when article number asked
    def query_specific(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        anns_field: str = "text_sparse_hierarchy",
        collection_name: str = settings.MILVUS_MAIN_NAME,
        partitions: list[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Perform specific sparse vector search using keyword matching
        """
        nums = extract_integers(text_query)
        expr = self.build_like_or_expr(nums)
        results = self.client.search(
            collection_name=collection_name,
            data=[text_query],
            anns_field=anns_field,
            filter=expr,
            top_k=top_k,
            output_fields=["text", "metadata", "hierarchy_path"],
            partitions=partitions,
        )

        # Parse results
        search_results = self._parse_results(results)

        return search_results

    def _create_schema(self):
        schema = MilvusClient.create_schema(auto_id=False)
        schema.add_field(
            field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=100
        )
        schema.add_field(
            field_name="text",
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
        )
        schema.add_field(
            field_name="hierarchy_path",
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            default_value="None in hierarchy_path",
        )
        schema.add_field(
            field_name="text_dense",
            datatype=DataType.FLOAT_VECTOR,
            dim=settings.EMBEDDING_DIM,
        )
        schema.add_field(
            field_name="text_sparse", datatype=DataType.SPARSE_FLOAT_VECTOR
        )
        schema.add_field(
            field_name="text_sparse_hierarchy", datatype=DataType.SPARSE_FLOAT_VECTOR
        )
        schema.add_field(field_name="metadata", datatype=DataType.JSON)

        # Add BM25 function to schema
        bm25_text = Function(
            name="text_bm25_emb",
            input_field_names=["text"],
            output_field_names=["text_sparse"],
            function_type=FunctionType.BM25,
        )

        # Add BM25 function to schema for hierarchy match
        bm25_hierarchy = Function(
            name="text_bm25_emb_hierarchy",
            input_field_names=["hierarchy_path"],
            output_field_names=["text_sparse_hierarchy"],
            function_type=FunctionType.BM25,
        )
        schema.add_function(bm25_text)  # For text match
        schema.add_function(bm25_hierarchy)  # For hierarchy match

        return schema

    def _create_index(self):
        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="text_dense",
            index_name="text_dense_index",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        index_params.add_index(
            field_name="text_sparse",
            index_name="text_sparse_index",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"inverted_index_algo": "DAAT_MAXSCORE"},
        )
        index_params.add_index(
            field_name="text_sparse_hierarchy",
            index_name="text_sparse_hierarchy_index",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"inverted_index_algo": "DAAT_MAXSCORE"},
        )
        return index_params

    def _parse_results(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Format results
        formatted_results = []
        for hit in results[0]:  # results[0] contains the merged and reranked hits
            metadata = hit.data["entity"]["metadata"]
            text = hit.data["entity"]["text"]
            if "hierarchy_path" in hit.data["entity"]:
                hierarchy_path = hit.data["entity"]["hierarchy_path"]
            else:
                hierarchy_path = None
            metadata["text"] = text
            formatted_results.append(
                {
                    "id": hit.id,
                    "score": hit.score,
                    "metadata": metadata,
                    "hierarchy_path": hierarchy_path if hierarchy_path else None,
                }
            )

        return formatted_results

    def delete_collection(
        self, collection_name: str = settings.MILVUS_MAIN_NAME
    ) -> None:
        if self.client.has_collection(collection_name):
            self.client.drop_collection(collection_name)
            logger.info(f"Deleted collection {collection_name}")

    def build_like_or_expr(self, values: Iterable[int | str]) -> str:
        """
        Build a Milvus boolean filter like:
        hierarchy_path like "%115%" or hierarchy_path like "%114%"
        """
        field = """metadata["article_number"]"""

        # deduplicate while preserving order
        seen, uniq = set(), []
        for v in values:
            s = str(v)
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        if not uniq:
            return ""

        expr = " or ".join(f"{field} == {v}" for v in uniq)

        logger.info(f"Build like or expression: {expr}")

        return expr
