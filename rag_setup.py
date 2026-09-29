"""Vertex AI RAG Engine + Vector Search 인덱스/엔드포인트 구성 도구.

사용법 (run.bat rag_setup.py <명령>):
    check            인증·프로젝트·리전 확인, 현재 리소스 상태 출력
    create-index     Vector Search 인덱스 생성 (STREAM_UPDATE, 비어 있는 상태로)
    create-endpoint  인덱스 엔드포인트 생성 (public endpoint)
    deploy           인덱스를 엔드포인트에 배포 (첫 배포 ~30분, 이때부터 시간당 과금)
    create-corpus    RAG 코퍼스 생성 (위 인덱스/엔드포인트를 벡터 DB로 연결)
    all              create-index -> create-endpoint -> deploy -> create-corpus 순서로 (이미 있는 건 건너뜀)
    upload <로컬 폴더|파일>   GCS 버킷(없으면 생성)에 올리고 코퍼스로 가져오기
    import <gs://...|drive URL> [...]   이미 GCS/Drive에 있는 파일 가져오기
    query "<질문>"   검색 테스트
    undeploy         배포 해제 (과금 중지; 인덱스·엔드포인트·코퍼스는 남음)

생성된 리소스 이름은 resources.json에 저장합니다(키 아님, git에 올려 집 PC와 공유).
"""
import json
import os
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

warnings.filterwarnings("ignore")  # SDK deprecated/experimental 경고

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "resources.json"

load_dotenv(ROOT / ".env")

PROJECT = os.getenv("GCP_PROJECT_ID", "gen-lang-client-0939908286")
LOCATION = os.getenv("GCP_LOCATION", "asia-northeast3")
PREFIX = os.getenv("RAG_NAME_PREFIX", "rag")
EMBED_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "text-multilingual-embedding-002")
DIMENSIONS = int(os.getenv("RAG_EMBEDDING_DIM", "768"))
DISTANCE = os.getenv("RAG_DISTANCE", "DOT_PRODUCT_DISTANCE")  # 또는 COSINE_DISTANCE
MACHINE_TYPE = os.getenv("RAG_DEPLOY_MACHINE", "e2-standard-2")
DEPLOYED_INDEX_ID = os.getenv("RAG_DEPLOYED_INDEX_ID", f"{PREFIX}_deployed_index").replace("-", "_")


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def aip():
    from google.cloud import aiplatform

    aiplatform.init(project=PROJECT, location=LOCATION)
    return aiplatform


def rag_client():
    import agentplatform

    return agentplatform.Client(project=PROJECT, location=LOCATION)


def cmd_check() -> None:
    import google.auth

    try:
        creds, adc_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    except google.auth.exceptions.DefaultCredentialsError:
        sys.exit("인증 정보가 없습니다. README의 '인증' 절을 보세요 (gcloud auth application-default login).")
    print(f"인증 OK ({type(creds).__name__}), ADC 기본 프로젝트: {adc_project}")
    print(f"대상 프로젝트: {PROJECT} / 리전: {LOCATION}")
    print(f"임베딩: {EMBED_MODEL} ({DIMENSIONS}차원, {DISTANCE})")
    state = load_state()
    for key in ("index", "index_endpoint", "deployed_index_id", "corpus"):
        print(f"  {key:18} {state.get(key, '-')}")
    rag_client().rag.list_corpora()  # 리전에서 RAG Engine API가 열려 있는지 확인
    print("RAG Engine API 호출 OK")


def cmd_create_index() -> None:
    state = load_state()
    if state.get("index"):
        print(f"이미 있음: {state['index']}")
        return
    a = aip()
    from google.cloud.aiplatform.matching_engine.matching_engine_index_config import DistanceMeasureType

    print("인덱스 생성 중 (수 분 걸릴 수 있음)...")
    index = a.MatchingEngineIndex.create_tree_ah_index(
        display_name=f"{PREFIX}-index",
        dimensions=DIMENSIONS,
        approximate_neighbors_count=150,
        leaf_node_embedding_count=500,
        leaf_nodes_to_search_percent=7,
        index_update_method="STREAM_UPDATE",  # RAG Engine은 STREAM_UPDATE만 지원
        distance_measure_type=DistanceMeasureType[DISTANCE],
        shard_size="SHARD_SIZE_SMALL",  # e2-standard-2 배포 가능
        description="RAG Engine vector store",
    )
    state["index"] = index.resource_name
    save_state(state)
    print(f"생성: {index.resource_name}")


def cmd_create_endpoint() -> None:
    state = load_state()
    if state.get("index_endpoint"):
        print(f"이미 있음: {state['index_endpoint']}")
        return
    a = aip()
    print("인덱스 엔드포인트 생성 중...")
    endpoint = a.MatchingEngineIndexEndpoint.create(
        display_name=f"{PREFIX}-index-endpoint",
        public_endpoint_enabled=True,  # RAG Engine은 public endpoint 지원
        description="RAG Engine index endpoint",
    )
    state["index_endpoint"] = endpoint.resource_name
    save_state(state)
    print(f"생성: {endpoint.resource_name}")


def cmd_deploy() -> None:
    state = load_state()
    if not (state.get("index") and state.get("index_endpoint")):
        sys.exit("먼저 create-index, create-endpoint를 실행하세요.")
    a = aip()
    endpoint = a.MatchingEngineIndexEndpoint(state["index_endpoint"])
    if any(d.id == DEPLOYED_INDEX_ID for d in endpoint.deployed_indexes):
        print(f"이미 배포됨: {DEPLOYED_INDEX_ID}")
    else:
        print(f"배포 중 ({MACHINE_TYPE} x1). 첫 배포는 약 30분 걸립니다...")
        endpoint.deploy_index(
            index=a.MatchingEngineIndex(state["index"]),
            deployed_index_id=DEPLOYED_INDEX_ID,
            machine_type=MACHINE_TYPE,
            min_replica_count=1,
            max_replica_count=1,
        )
        print("배포 완료")
    state["deployed_index_id"] = DEPLOYED_INDEX_ID
    save_state(state)


def cmd_undeploy() -> None:
    state = load_state()
    a = aip()
    endpoint = a.MatchingEngineIndexEndpoint(state["index_endpoint"])
    endpoint.undeploy_index(deployed_index_id=state.get("deployed_index_id", DEPLOYED_INDEX_ID))
    state.pop("deployed_index_id", None)
    save_state(state)
    print("배포 해제 완료 (다시 쓰려면 deploy)")


def cmd_create_corpus() -> None:
    from agentplatform import types

    state = load_state()
    if state.get("corpus"):
        print(f"이미 있음: {state['corpus']}")
        return
    if not state.get("deployed_index_id"):
        sys.exit("먼저 deploy를 완료하세요.")
    model = f"projects/{PROJECT}/locations/{LOCATION}/publishers/google/models/{EMBED_MODEL}"
    corpus = rag_client().rag.create_corpus(
        rag_corpus=types.RagCorpus(
            display_name=f"{PREFIX}-corpus",
            description="RAG corpus backed by Vector Search",
            rag_vector_db_config=types.RagVectorDbConfig(
                rag_embedding_model_config=types.RagEmbeddingModelConfig(
                    vertex_prediction_endpoint=types.RagEmbeddingModelConfigVertexPredictionEndpoint(endpoint=model)
                ),
                vertex_vector_search=types.RagVectorDbConfigVertexVectorSearch(
                    index=state["index"], index_endpoint=state["index_endpoint"]
                ),
            ),
        )
    )
    state["corpus"] = corpus.name
    save_state(state)
    print(f"코퍼스 생성: {corpus.name}")


def cmd_import(sources: list[str]) -> None:
    from agentplatform import types

    if not sources:
        sys.exit("사용법: import gs://bucket/path/ [https://drive.google.com/...]")
    corpus = load_state()["corpus"]
    gcs = [s for s in sources if s.startswith("gs://")]
    drive = [s for s in sources if not s.startswith("gs://")]
    kwargs = {
        "rag_file_chunking_config": {"chunk_size": 512, "chunk_overlap": 100},
        "max_embedding_requests_per_min": 900,
    }
    if gcs:
        kwargs["gcs_source"] = {"uris": gcs}
    if drive:
        kwargs["google_drive_source"] = {"resource_ids": [
            {"resource_id": d.rstrip("/").split("/")[-1].split("?")[0],
             "resource_type": "RESOURCE_TYPE_FOLDER" if "folders" in d else "RESOURCE_TYPE_FILE"}
            for d in drive]}
    try:
        resp = rag_client().rag.import_files(name=corpus, import_config=types.ImportRagFilesConfig(**kwargs))
    except (ValueError, RuntimeError) as e:
        msg = str(e)
        if "does not belong to project" in msg:
            sys.exit(f"버킷을 찾을 수 없습니다(이 프로젝트 소유가 아님): {gcs}\n로컬 폴더라면: upload <폴더>")
        if "Google Drive" in msg:
            sys.exit(f"Drive 폴더/파일을 찾을 수 없습니다: {drive}\n"
                     f"1) 링크의 ID가 맞는지  2) {rag_service_agent()} 에 뷰어로 공유했는지 확인하세요.")
        raise
    r = resp.model_dump(exclude_none=True) if hasattr(resp, "model_dump") else resp
    print(f"가져오기 완료: {r}")


def rag_service_agent() -> str:
    number = load_state()["corpus"].split("/")[1]
    return f"service-{number}@gcp-sa-vertex-rag.iam.gserviceaccount.com"


def cmd_upload(args: list[str]) -> None:
    """로컬 폴더(또는 파일)를 GCS 버킷에 올린 뒤 코퍼스로 가져오기. 버킷이 없으면 같은 리전에 만듦."""
    from google.cloud import storage

    if not args:
        sys.exit("사용법: upload <로컬 폴더 또는 파일>")
    src = Path(args[0]).resolve()
    if not src.exists():
        sys.exit(f"없는 경로: {src}")
    bucket_name = os.getenv("RAG_BUCKET", f"{PROJECT}-rag-docs")
    client = storage.Client(project=PROJECT)
    bucket = client.lookup_bucket(bucket_name)
    if bucket is None:
        print(f"버킷 생성: gs://{bucket_name} ({LOCATION})")
        bucket = client.create_bucket(bucket_name, location=LOCATION)
    files = [src] if src.is_file() else [f for f in src.rglob("*") if f.is_file()]
    base = src.parent if src.is_file() else src
    prefix = f"docs/{src.stem if src.is_file() else src.name}/"
    for f in files:
        blob = prefix + f.relative_to(base).as_posix()
        bucket.blob(blob).upload_from_filename(str(f))
        print(f"  업로드 {f.name}")
    print(f"{len(files)}개 업로드 -> gs://{bucket_name}/{prefix}")
    cmd_import([f"gs://{bucket_name}/{prefix}"])


def cmd_query(text: str) -> None:
    if not text:
        sys.exit('사용법: query "질문"')
    corpus = load_state()["corpus"]
    resp = rag_client().rag.retrieve_contexts(
        vertex_rag_store={"rag_resources": [{"rag_corpus": corpus}]},
        query={"text": text, "similarity_top_k": 5},
    )
    contexts = (resp.contexts.contexts if resp.contexts else None) or []
    if not contexts:
        print("검색 결과 없음 (코퍼스에 문서가 없거나 관련 내용이 없음). 먼저 upload / import 하세요.")
    for ctx in contexts:
        print(f"[{ctx.score or ctx.distance or 0:.3f}] {ctx.source_uri}\n  {(ctx.text or '')[:200]}\n")


def main() -> None:
    args = sys.argv[1:] or ["check"]
    cmd, rest = args[0], args[1:]
    steps = {
        "check": cmd_check,
        "create-index": cmd_create_index,
        "create-endpoint": cmd_create_endpoint,
        "deploy": cmd_deploy,
        "create-corpus": cmd_create_corpus,
        "undeploy": cmd_undeploy,
    }
    if cmd == "all":
        for step in ("create-index", "create-endpoint", "deploy", "create-corpus"):
            steps[step]()
    elif cmd == "import":
        cmd_import(rest)
    elif cmd == "upload":
        cmd_upload(rest)
    elif cmd == "query":
        cmd_query(" ".join(rest))
    elif cmd in steps:
        steps[cmd]()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
