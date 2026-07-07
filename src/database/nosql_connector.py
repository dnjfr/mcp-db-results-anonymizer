"""MongoDB connector with operator and stage security validation.

Provides secure read operations (find, aggregate, sample)
with blocking of dangerous operators ($where, $out, $merge, etc.).
"""

from urllib.parse import quote_plus

from pymongo import MongoClient
from pymongo.database import Database


class NoSQLConnector:
    """Secure MongoDB connector with dangerous operation blocking."""

    def __init__(self, config: dict):
        """Initialize the MongoDB connection.

        Args:
            config: Configuration dictionary with keys: user, password, host, port,
                    database and optionally auth_database (default: 'admin').
        """
        user = config["user"]
        password = config["password"]
        host = config["host"]
        port = config["port"]
        database = config["database"]
        auth_db = config.get("auth_database", "admin")
        uri = f"mongodb://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}?authSource={auth_db}"
        self._client = MongoClient(
            uri,
            maxPoolSize=10,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=30000,
        )
        self._db: Database = self._client[database]

    def list_collections(self) -> list[str]:
        """List all collections in the MongoDB database.

        Returns:
            Sorted list of collection names.
        """
        return sorted(self._db.list_collection_names())

    _BLOCKED_OPERATORS = frozenset({
        "$where", "$function", "$accumulator", "$expr",
    })

    _BLOCKED_STAGES = frozenset({
        "$out", "$merge", "$lookup", "$graphlookup",
    })

    def _check_filter(self, obj, path: str = ""):
        """Recursively check that a filter or pipeline contains no blocked operators.

        Args:
            obj: Object to check (dict, list or scalar).
            path: Current path in the tree for debugging.

        Returns:
            None

        Raises:
            ValueError: If a blocked operator or stage is found.
        """
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key.startswith("$"):
                    lower_key = key.lower()
                    if lower_key in self._BLOCKED_OPERATORS:
                        raise ValueError(f"Opérateur MongoDB interdit: {key}")
                    if lower_key in self._BLOCKED_STAGES:
                        raise ValueError(f"Stage MongoDB interdit (écriture): {key}")
                self._check_filter(value, f"{path}.{key}")
        elif isinstance(obj, list):
            for item in obj:
                self._check_filter(item, path)

    def find(self, collection: str, filter_: dict | None = None,
             projection: dict | None = None, limit: int = 100) -> list[dict]:
        """Execute a MongoDB find query with filter security validation.

        Args:
            collection: Collection name to query.
            filter_: MongoDB filter (default: {} for all documents).
            projection: MongoDB projection to select fields.
            limit: Maximum number of documents to return (default: 100).

        Returns:
            List of dicts representing documents (ObjectId converted to str).

        Raises:
            ValueError: If the filter contains blocked operators.
        """
        safe_filter = filter_ or {}
        self._check_filter(safe_filter)
        if projection:
            self._check_filter(projection)
        cursor = self._db[collection].find(safe_filter, projection)
        docs = []
        for doc in cursor.limit(limit):
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs

    def sample(self, collection: str, size: int = 100) -> list[dict]:
        """Randomly sample documents from a collection via $sample.

        Args:
            collection: Collection name.
            size: Number of documents to sample (default: 100).

        Returns:
            List of dicts representing the sampled documents.
        """
        pipeline = [{"$sample": {"size": size}}]
        docs = []
        for doc in self._db[collection].aggregate(pipeline):
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs

    def infer_schema(self, collection: str, sample_size: int = 100) -> list[dict]:
        """Infer a collection's schema by sampling documents.

        Analyzes a sample of documents to extract keys, their types
        and their frequency of occurrence.

        Args:
            collection: Collection name.
            sample_size: Number of documents to sample (default: 100).

        Returns:
            List of dicts with keys: name (dotted path), types (Python types),
            frequency (proportion of documents containing the key).
        """
        docs = self.sample(collection, sample_size)
        if not docs:
            return []
        keys_info: dict[str, dict] = {}
        for doc in docs:
            self._extract_keys(doc, "", keys_info, len(docs))
        return [
            {
                "name": key,
                "types": sorted(info["types"]),
                "frequency": round(info["count"] / len(docs), 2),
            }
            for key, info in sorted(keys_info.items())
        ]

    def _extract_keys(self, obj: dict, prefix: str,
                      keys_info: dict, total: int):
        """Recursively extract keys and their types from a MongoDB document.

        Args:
            obj: Dict representing a document or sub-document.
            prefix: Path prefix for nested keys.
            keys_info: Accumulator dict {key: {types: set, count: int}}.
            total: Total number of documents (for frequency calculation).

        Returns:
            None (modifies keys_info in place).
        """
        for key, value in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if full_key not in keys_info:
                keys_info[full_key] = {"types": set(), "count": 0}
            keys_info[full_key]["types"].add(type(value).__name__)
            keys_info[full_key]["count"] += 1
            if isinstance(value, dict):
                self._extract_keys(value, full_key, keys_info, total)

    def _check_pipeline(self, pipeline: list[dict]):
        """Check that an aggregation pipeline contains no blocked write stages.

        Args:
            pipeline: List of MongoDB aggregation stages.

        Returns:
            None

        Raises:
            ValueError: If a blocked stage ($out, $merge) or dangerous operator is found.
        """
        for stage in pipeline:
            for key in stage:
                if key.lower() in self._BLOCKED_STAGES:
                    raise ValueError(f"Stage MongoDB interdit (écriture): {key}")
            self._check_filter(stage)

    def aggregate(self, collection: str, pipeline: list[dict],
                  limit: int = 100) -> list[dict]:
        """Execute a MongoDB aggregation pipeline with security validation.

        Automatically appends a $limit stage if the pipeline does not contain one.

        Args:
            collection: Collection name.
            pipeline: List of MongoDB aggregation stages.
            limit: Maximum number of output documents (default: 100).

        Returns:
            List of dicts resulting from the aggregation.

        Raises:
            ValueError: If the pipeline contains blocked write stages.
        """
        self._check_pipeline(pipeline)
        pipeline = [dict(s) for s in pipeline]
        has_limit = any("$limit" in stage for stage in pipeline)
        if has_limit:
            for stage in pipeline:
                if "$limit" in stage:
                    stage["$limit"] = min(stage["$limit"], limit)
        else:
            pipeline.append({"$limit": limit})
        docs = []
        for doc in self._db[collection].aggregate(pipeline):
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs

    @property
    def db(self) -> Database:
        """Return the underlying MongoDB database instance.

        Returns:
            pymongo.database.Database instance.
        """
        return self._db
