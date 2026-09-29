# rag-engine

Vertex AI RAG Engine 코퍼스와 그 벡터 저장소(Vector Search 인덱스 + 인덱스 엔드포인트)를 코드로 만들고 관리합니다.
GCP 프로젝트: `gen-lang-client-0939908286`

## 구조

```
[문서: GCS / Google Drive]
      │ import (청크 분할 + 임베딩)
      ▼
RAG 코퍼스 ──rag_vector_db_config──▶ Vector Search 인덱스 (STREAM_UPDATE)
      │                                  └ 인덱스 엔드포인트 (public)에 배포
      ▼ retrieve_contexts
[Claude / Notion AI / 앱]  ← MCP 서버나 API로 연결 (나중에)
```

- **이 폴더에서 관리하는 것**: 인프라 설정(리전, 임베딩 모델, 차원), 생성 스크립트, 리소스 이름(`resources.json`).
- **GCP가 관리하는 것**: 실제 벡터와 문서 청크. 로컬에는 벡터를 저장하지 않습니다.
- **Claude·Notion AI 연동**은 이 코퍼스 위에 얹는 별도 계층입니다(검색 API를 부르는 MCP 서버 등). 인프라가 준비된 뒤 추가합니다.

## 인증 (1회)

이 PC에는 아직 gcloud가 없습니다. 둘 중 하나를 쓰세요.

1. **gcloud ADC (권장)**: Google Cloud CLI를 설치한 뒤
   `gcloud auth application-default login` 과
   `gcloud auth application-default set-quota-project gen-lang-client-0939908286`
2. **서비스 계정 키**: 콘솔에서 `Vertex AI User` 역할로 키(JSON)를 받아 `secrets\sa.json`에 두고
   `.env`에 `GOOGLE_APPLICATION_CREDENTIALS=./secrets/sa.json`을 적습니다(조직 정책상 키 생성이 막혀 있을 수 있음).

필요한 API: **Vertex AI API** (`aiplatform.googleapis.com`) 사용 설정.

## 실행

```
.\run.bat rag_setup.py check            # 인증·리전·현재 리소스 확인
.\run.bat rag_setup.py all              # 인덱스 → 엔드포인트 → 배포(~30분) → 코퍼스
.\run.bat rag_setup.py upload D:\문서폴더      # 로컬 폴더 -> 버킷(없으면 생성) -> 코퍼스
.\run.bat rag_setup.py import gs://버킷/폴더/ https://drive.google.com/drive/folders/ID
.\run.bat rag_setup.py query "질문"
.\run.bat rag_setup.py undeploy         # 안 쓸 때 과금 중지
```

단계별 실행도 됩니다: `create-index`, `create-endpoint`, `deploy`, `create-corpus`. 이미 만든 단계는 건너뜁니다.

## 주의

- **비용**: 인덱스를 엔드포인트에 배포한 순간부터 노드(기본 `e2-standard-2` x1)가 시간 단위로 과금됩니다. 쓰지 않을 때는 `undeploy`.
- 인덱스는 반드시 `STREAM_UPDATE`여야 하고, 거리 측정은 `DOT_PRODUCT_DISTANCE` 또는 `COSINE_DISTANCE`만 됩니다.
- 인덱스 차원(`RAG_EMBEDDING_DIM`)은 코퍼스 임베딩 모델과 같아야 합니다. 인덱스를 만든 뒤에는 바꿀 수 없습니다.
- RAG Engine, 인덱스, 엔드포인트는 같은 리전에 있어야 합니다. `check`에서 리전 오류가 나면 `.env`의 `GCP_LOCATION`을 바꾸세요.
- Drive 문서를 가져오려면 해당 폴더를 Vertex AI RAG 서비스 에이전트
  (`service-<프로젝트번호>@gcp-sa-vertex-rag.iam.gserviceaccount.com`)에 뷰어로 공유해야 합니다.
