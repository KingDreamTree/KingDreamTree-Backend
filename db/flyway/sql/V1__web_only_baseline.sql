--
-- PostgreSQL database dump
--


-- Dumped from database version 17.11
-- Dumped by pg_dump version 17.12 (Debian 17.12-1.pgdg13+1)

SET LOCAL statement_timeout = 0;
SET LOCAL lock_timeout = 0;
SET LOCAL idle_in_transaction_session_timeout = 0;
SET LOCAL transaction_timeout = 0;
SET LOCAL client_encoding = 'UTF8';
SET LOCAL standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', true);
SET LOCAL check_function_bodies = false;
SET LOCAL xmloption = content;
SET LOCAL client_min_messages = warning;
SET LOCAL row_security = off;

--
-- Name: merge_contraindications(uuid, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.merge_contraindications(p_session_id uuid, p_added jsonb) RETURNS jsonb
    LANGUAGE sql
    AS $$
    UPDATE analysis_session AS s
    SET contraindications = COALESCE(s.contraindications, '[]'::jsonb) || (
        SELECT COALESCE(jsonb_agg(a.elem ORDER BY a.ord), '[]'::jsonb)
        FROM jsonb_array_elements(COALESCE(p_added, '[]'::jsonb)) WITH ORDINALITY AS a(elem, ord)
        WHERE NOT (COALESCE(s.contraindications, '[]'::jsonb) @> jsonb_build_array(a.elem))
    )
    WHERE s.session_id = p_session_id
    RETURNING s.contraindications;
$$;


SET LOCAL default_tablespace = '';

SET LOCAL default_table_access_method = heap;

--
-- Name: analysis_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_session (
    session_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    reference_source character varying(20) DEFAULT 'USER_UPLOAD'::character varying NOT NULL,
    contraindications jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT analysis_session_reference_source_check CHECK (((reference_source)::text = ANY ((ARRAY['USER_UPLOAD'::character varying, 'PRESET'::character varying])::text[]))),
    CONSTRAINT analysis_session_status_check CHECK (((status)::text = ANY ((ARRAY['ACTIVE'::character varying, 'ARCHIVED'::character varying])::text[])))
);


--
-- Name: body_part; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.body_part (
    class_name character varying(40) NOT NULL,
    name_ko character varying(40) NOT NULL,
    part_group character varying(20) NOT NULL,
    inbody_segment character varying(20),
    is_comparable boolean DEFAULT false NOT NULL,
    color_hex character(7),
    display_order smallint DEFAULT 0 NOT NULL,
    CONSTRAINT body_part_color_hex_check CHECK ((color_hex ~ '^#[0-9A-Fa-f]{6}$'::text)),
    CONSTRAINT body_part_inbody_segment_check CHECK (((inbody_segment)::text = ANY ((ARRAY['LEFT_ARM'::character varying, 'RIGHT_ARM'::character varying, 'TRUNK'::character varying, 'LEFT_LEG'::character varying, 'RIGHT_LEG'::character varying])::text[]))),
    CONSTRAINT body_part_part_group_check CHECK (((part_group)::text = ANY ((ARRAY['UPPER'::character varying, 'CORE'::character varying, 'LOWER'::character varying, 'OTHER'::character varying])::text[])))
);


--
-- Name: TABLE body_part; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.body_part IS 'Sapiens2 전체 클래스 마스터. 비교 대상은 is_comparable=true 9개.';


--
-- Name: COLUMN body_part.color_hex; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.body_part.color_hex IS '오버레이 색. NULL이면 칠하지 않음(배경·옷 등).';


--
-- Name: body_part_segment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.body_part_segment (
    segment_id uuid DEFAULT gen_random_uuid() NOT NULL,
    segmentation_id uuid NOT NULL,
    class_name character varying(40) NOT NULL,
    label_value smallint NOT NULL,
    pixel_count integer NOT NULL,
    area_ratio real NOT NULL,
    bbox_x integer NOT NULL,
    bbox_y integer NOT NULL,
    bbox_w integer NOT NULL,
    bbox_h integer NOT NULL,
    is_truncated boolean DEFAULT false NOT NULL,
    is_valid boolean NOT NULL,
    invalid_reason character varying(30),
    crop_bucket character varying(63),
    crop_path character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    clothing_pixel_count integer,
    CONSTRAINT body_part_segment_area_ratio_check CHECK (((area_ratio >= (0)::double precision) AND (area_ratio <= (1)::double precision))),
    CONSTRAINT body_part_segment_bbox_h_check CHECK ((bbox_h > 0)),
    CONSTRAINT body_part_segment_bbox_w_check CHECK ((bbox_w > 0)),
    CONSTRAINT body_part_segment_bbox_x_check CHECK ((bbox_x >= 0)),
    CONSTRAINT body_part_segment_bbox_y_check CHECK ((bbox_y >= 0)),
    CONSTRAINT body_part_segment_clothing_px_chk CHECK (((clothing_pixel_count IS NULL) OR ((clothing_pixel_count >= 0) AND (clothing_pixel_count <= pixel_count)))),
    CONSTRAINT body_part_segment_invalid_reason_check CHECK (((invalid_reason)::text = ANY ((ARRAY['TOO_SMALL'::character varying, 'TOO_SMALL_RATIO'::character varying, 'TRUNCATED'::character varying, 'NOT_COMPARABLE'::character varying, 'MOSTLY_CLOTHING'::character varying])::text[]))),
    CONSTRAINT body_part_segment_label_value_check CHECK (((label_value >= 0) AND (label_value <= 255))),
    CONSTRAINT body_part_segment_pixel_count_check CHECK ((pixel_count >= 0))
);


--
-- Name: COLUMN body_part_segment.label_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.body_part_segment.label_value IS '이 맵에서의 픽셀 값. ⚠️ 조인 키로 쓰지 말 것 — 조인은 항상 class_name.';


--
-- Name: COLUMN body_part_segment.bbox_x; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.body_part_segment.bbox_x IS '맵 좌표계 기준. 원본 위에 그리려면 photo.width/segmentation.map_width 배율로 스케일.';


--
-- Name: COLUMN body_part_segment.crop_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.body_part_segment.crop_path IS 'VLM 입력용 파생 캐시. 맵에서 언제든 재생성 가능하므로 NULL 허용.';


--
-- Name: COLUMN body_part_segment.clothing_pixel_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.body_part_segment.clothing_pixel_count IS 'pixel_count 중 옷에서 흡수된 픽셀 수 (app/services/part_merge.py). NULL = 병합 미적용(구버전 행), 0 = 병합 적용했고 흡수 없음. 이 구분을 위해 NOT NULL DEFAULT 0 으로 만들지 말 것.';


--
-- Name: exercise_catalog; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exercise_catalog (
    exercise_ref character varying(40) NOT NULL,
    name_en character varying(200) NOT NULL,
    name_ko character varying(200),
    body_parts jsonb NOT NULL,
    target_muscles jsonb NOT NULL,
    secondary_muscles jsonb,
    equipments jsonb NOT NULL,
    exercise_type character varying(20) NOT NULL,
    keywords jsonb,
    image_url character varying(500),
    is_beginner_safe boolean DEFAULT false NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE exercise_catalog; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.exercise_catalog IS 'ExerciseDB 운동 풀 캐시. LLM 은 이 안의 exercise_ref 중에서만 고른다 — 후보 밖 운동을 낼 수 없으므로 환각이 구조적으로 차단된다.';


--
-- Name: COLUMN exercise_catalog.is_beginner_safe; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.exercise_catalog.is_beginner_safe IS '초보자에게 처방해도 되는지. ⚠️ DEFAULT false — 검토되지 않은 행이 안전한 것으로 취급되면 안 된다. seed 스크립트가 스크리닝 결과를 명시적으로 넣는다.';


--
-- Name: inbody; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inbody (
    inbody_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    device_type character varying(30),
    measured_at date,
    age integer,
    gender character varying(10),
    height numeric(5,1),
    weight numeric(5,1),
    bmi numeric(4,1),
    body_fat_mass numeric(5,1),
    body_fat_percentage numeric(4,1),
    skeletal_muscle_mass numeric(5,1),
    fat_free_mass numeric(5,1),
    bmr_kcal integer,
    raw_ocr jsonb,
    validation jsonb,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    validation_error text,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT inbody_age_check CHECK (((age >= 1) AND (age <= 120))),
    CONSTRAINT inbody_bmi_check CHECK (((bmi >= (10)::numeric) AND (bmi <= (60)::numeric))),
    CONSTRAINT inbody_bmr_kcal_check CHECK (((bmr_kcal >= 500) AND (bmr_kcal <= 5000))),
    CONSTRAINT inbody_body_fat_mass_check CHECK (((body_fat_mass >= (0)::numeric) AND (body_fat_mass <= (150)::numeric))),
    CONSTRAINT inbody_body_fat_percentage_check CHECK (((body_fat_percentage >= (1)::numeric) AND (body_fat_percentage <= (70)::numeric))),
    CONSTRAINT inbody_fat_free_mass_check CHECK (((fat_free_mass >= (10)::numeric) AND (fat_free_mass <= (150)::numeric))),
    CONSTRAINT inbody_gender_check CHECK (((gender)::text = ANY ((ARRAY['MALE'::character varying, 'FEMALE'::character varying])::text[]))),
    CONSTRAINT inbody_height_check CHECK (((height >= (120)::numeric) AND (height <= (220)::numeric))),
    CONSTRAINT inbody_skeletal_muscle_mass_check CHECK (((skeletal_muscle_mass >= (10)::numeric) AND (skeletal_muscle_mass <= (60)::numeric))),
    CONSTRAINT inbody_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'DONE'::character varying, 'FAILED'::character varying])::text[]))),
    CONSTRAINT inbody_weight_check CHECK (((weight >= (25)::numeric) AND (weight <= (250)::numeric)))
);


--
-- Name: TABLE inbody; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.inbody IS '결과지 이미지는 저장하지 않음. 임시 경로는 job.payload에. 항등식 검증은 애플리케이션에서 → validation.';


--
-- Name: inbody_segment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inbody_segment (
    inbody_segment_id uuid DEFAULT gen_random_uuid() NOT NULL,
    inbody_id uuid NOT NULL,
    segment character varying(20) NOT NULL,
    lean_mass numeric(5,1),
    fat_mass numeric(5,1),
    CONSTRAINT inbody_segment_fat_mass_check CHECK ((fat_mass >= (0)::numeric)),
    CONSTRAINT inbody_segment_lean_mass_check CHECK ((lean_mass >= (0)::numeric)),
    CONSTRAINT inbody_segment_segment_check CHECK (((segment)::text = ANY ((ARRAY['LEFT_ARM'::character varying, 'RIGHT_ARM'::character varying, 'TRUNK'::character varying, 'LEFT_LEG'::character varying, 'RIGHT_LEG'::character varying])::text[])))
);


--
-- Name: TABLE inbody_segment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.inbody_segment IS '부위별 범위 검증(팔 0.5~8kg / 다리 2~20kg)은 CHECK가 아니라 애플리케이션에서 경고만.';


--
-- Name: job; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job (
    job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    kind character varying(30) NOT NULL,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    payload jsonb,
    result jsonb,
    error text,
    attempts integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT job_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT job_kind_check CHECK (((kind)::text = ANY ((ARRAY['SEG_REFERENCE'::character varying, 'SEG_USER'::character varying, 'OCR_INBODY'::character varying, 'VLM_PART'::character varying, 'VLM_OVERALL'::character varying, 'ROUTINE_GEN'::character varying, 'ROUTINE_PATCH'::character varying])::text[]))),
    CONSTRAINT job_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'DONE'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: COLUMN job.error; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.job.error IS '사용자에게 그대로 노출해도 되는 문구만. 스택 트레이스·모델 경로·API 키 금지.';


--
-- Name: month_routine; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.month_routine (
    month_routine_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    exercise_days_per_week integer NOT NULL,
    goal text,
    focus_areas jsonb,
    start_date date,
    generation_type character varying(20) DEFAULT 'INITIAL'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    raw_response jsonb,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT month_routine_exercise_days_per_week_check CHECK (((exercise_days_per_week >= 1) AND (exercise_days_per_week <= 7))),
    CONSTRAINT month_routine_generation_type_check CHECK (((generation_type)::text = ANY ((ARRAY['INITIAL'::character varying, 'DAYS_CHANGED'::character varying, 'FEEDBACK'::character varying])::text[]))),
    CONSTRAINT month_routine_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'DONE'::character varying, 'FAILED'::character varying])::text[]))),
    CONSTRAINT month_routine_version_check CHECK ((version >= 1))
);


--
-- Name: TABLE month_routine; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.month_routine IS '⚠️ 행을 삭제하지 말 것. is_active=false로만 내림. 삭제하면 workout_log가 CASCADE로 딸려 사라짐.';


--
-- Name: overall_diagnosis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.overall_diagnosis (
    overall_diagnosis_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    similarity_score smallint,
    score_source character varying(20) DEFAULT 'VLM'::character varying NOT NULL,
    score_rationale text,
    summary text,
    priority_parts jsonb,
    strengths jsonb,
    cautions jsonb,
    raw_response jsonb,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    prompt_version character varying(200),
    CONSTRAINT overall_diagnosis_score_source_check CHECK (((score_source)::text = ANY ((ARRAY['VLM'::character varying, 'RULE'::character varying])::text[]))),
    CONSTRAINT overall_diagnosis_similarity_score_check CHECK (((similarity_score >= 0) AND (similarity_score <= 100))),
    CONSTRAINT overall_diagnosis_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'DONE'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: part_diagnosis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.part_diagnosis (
    part_diagnosis_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    class_name character varying(40) NOT NULL,
    reference_segment_id uuid,
    user_segment_id uuid,
    vlm_input_type character varying(20) DEFAULT 'CROP'::character varying NOT NULL,
    differences jsonb,
    assessment text,
    gap_level character varying(20),
    priority smallint,
    confidence character varying(10),
    raw_response jsonb,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    prompt_version character varying(200),
    CONSTRAINT part_diagnosis_confidence_check CHECK (((confidence)::text = ANY ((ARRAY['LOW'::character varying, 'MEDIUM'::character varying, 'HIGH'::character varying])::text[]))),
    CONSTRAINT part_diagnosis_gap_level_check CHECK (((gap_level)::text = ANY ((ARRAY['NONE'::character varying, 'SLIGHT'::character varying, 'MODERATE'::character varying, 'SIGNIFICANT'::character varying])::text[]))),
    CONSTRAINT part_diagnosis_priority_check CHECK (((priority >= 1) AND (priority <= 5))),
    CONSTRAINT part_diagnosis_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'DONE'::character varying, 'FAILED'::character varying])::text[]))),
    CONSTRAINT part_diagnosis_vlm_input_type_check CHECK (((vlm_input_type)::text = ANY ((ARRAY['CROP'::character varying, 'HIGHLIGHT'::character varying])::text[])))
);


--
-- Name: TABLE part_diagnosis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.part_diagnosis IS '부위 하나가 FAILED여도 전체 중단하지 않음. 해당 행만 결과에서 제외.';


--
-- Name: photo; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.photo (
    photo_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    kind character varying(20) NOT NULL,
    storage_bucket character varying(63) DEFAULT 'photos'::character varying NOT NULL,
    storage_path character varying(500),
    width integer,
    height integer,
    capture_source character varying(20),
    pose_landmarks jsonb,
    pose_scale_basis character varying(20),
    pose_similarity numeric(5,2),
    framing_score numeric(4,3),
    pose_person_area_ratio real,
    multi_person boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    was_mirrored boolean DEFAULT false NOT NULL,
    facing_delta numeric(6,3),
    pose_oks numeric(4,3),
    crop_box jsonb,
    CONSTRAINT photo_capture_source_check CHECK (((capture_source)::text = ANY ((ARRAY['CAPTURE'::character varying, 'UPLOAD'::character varying])::text[]))),
    CONSTRAINT photo_facing_delta_check CHECK ((facing_delta >= (0)::numeric)),
    CONSTRAINT photo_framing_score_check CHECK (((framing_score >= (0)::numeric) AND (framing_score <= (1)::numeric))),
    CONSTRAINT photo_height_check CHECK ((height > 0)),
    CONSTRAINT photo_kind_check CHECK (((kind)::text = ANY ((ARRAY['REFERENCE'::character varying, 'USER'::character varying])::text[]))),
    CONSTRAINT photo_pose_oks_check CHECK (((pose_oks >= (0)::numeric) AND (pose_oks <= (1)::numeric))),
    CONSTRAINT photo_pose_person_area_ratio_check CHECK (((pose_person_area_ratio >= (0)::double precision) AND (pose_person_area_ratio <= (1)::double precision))),
    CONSTRAINT photo_pose_scale_basis_check CHECK (((pose_scale_basis)::text = ANY ((ARRAY['TORSO'::character varying, 'HIP_KNEE'::character varying])::text[]))),
    CONSTRAINT photo_pose_similarity_check CHECK (((pose_similarity >= (0)::numeric) AND (pose_similarity <= (100)::numeric))),
    CONSTRAINT photo_width_check CHECK ((width > 0))
);


--
-- Name: COLUMN photo.storage_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.photo.storage_path IS 'Storage 경로. PHOTO_PIPELINE=pod 로 만든 행은 NULL — 사진을 저장하지 않는다.';


--
-- Name: COLUMN photo.pose_landmarks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.photo.pose_landmarks IS '반전되지 않은 카메라 원본 기준. 미러링은 프론트 CSS만.';


--
-- Name: COLUMN photo.pose_person_area_ratio; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.photo.pose_person_area_ratio IS 'MediaPipe 기준 추정치(프레이밍 판정용). 정확한 인물 면적은 segmentation.person_pixel_count.';


--
-- Name: COLUMN photo.was_mirrored; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.photo.was_mirrored IS '거울 촬영으로 접수돼 서버가 좌우를 되돌려 저장했는지. 저장 이미지·랜드마크는 항상 비반전 기준.';


--
-- Name: COLUMN photo.crop_box; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.photo.crop_box IS '팟이 세그 전에 적용한 크롭 (비반전 원본 픽셀 좌표 + 원본 크기 + flipped). 프론트가 기기 원본을 같은 박스로 잘라 맵을 얹는다. storage 경로에서는 NULL.';


--
-- Name: prompt_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prompt_version (
    prompt_version_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(60) NOT NULL,
    version integer NOT NULL,
    content text NOT NULL,
    note text,
    is_active boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT prompt_version_version_check CHECK ((version >= 1))
);


--
-- Name: routine_day; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routine_day (
    routine_day_id uuid DEFAULT gen_random_uuid() NOT NULL,
    month_routine_id uuid NOT NULL,
    day_order smallint NOT NULL,
    title character varying(100),
    estimated_duration_min smallint,
    CONSTRAINT routine_day_day_order_check CHECK (((day_order >= 1) AND (day_order <= 7))),
    CONSTRAINT routine_day_estimated_duration_min_check CHECK ((estimated_duration_min > 0))
);


--
-- Name: COLUMN routine_day.day_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.routine_day.day_order IS '주기 내 순서 (1..N). **요일이 아니다** — 사용자가 언제 수행할지는 자유다. ⚠️ 행 수와 month_routine.exercise_days_per_week 의 일치는 DB 가 막지 못한다. 애플리케이션이 검증한다 (app/services/routine_repo.py).';


--
-- Name: routine_day_exercise; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routine_day_exercise (
    routine_day_exercise_id uuid DEFAULT gen_random_uuid() NOT NULL,
    routine_day_id uuid NOT NULL,
    order_index smallint NOT NULL,
    exercise_ref character varying(40),
    name character varying(100) NOT NULL,
    image_url character varying(500),
    exercise_kind character varying(10) DEFAULT 'STRENGTH'::character varying NOT NULL,
    muscle_group character varying(30),
    sets smallint,
    reps smallint,
    duration_min smallint,
    rest_sec smallint,
    rir smallint,
    boosted_by character varying(40),
    note text,
    CONSTRAINT routine_day_exercise_duration_min_check CHECK ((duration_min > 0)),
    CONSTRAINT routine_day_exercise_exercise_kind_check CHECK (((exercise_kind)::text = ANY ((ARRAY['STRENGTH'::character varying, 'CARDIO'::character varying])::text[]))),
    CONSTRAINT routine_day_exercise_kind_chk CHECK (((((exercise_kind)::text = 'STRENGTH'::text) AND (sets IS NOT NULL)) OR (((exercise_kind)::text = 'CARDIO'::text) AND (duration_min IS NOT NULL)))),
    CONSTRAINT routine_day_exercise_order_index_check CHECK ((order_index > 0)),
    CONSTRAINT routine_day_exercise_reps_check CHECK ((reps > 0)),
    CONSTRAINT routine_day_exercise_rest_sec_check CHECK ((rest_sec >= 0)),
    CONSTRAINT routine_day_exercise_rir_check CHECK (((rir >= 0) AND (rir <= 5))),
    CONSTRAINT routine_day_exercise_sets_check CHECK ((sets > 0))
);


--
-- Name: routine_revision; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routine_revision (
    routine_revision_id uuid DEFAULT gen_random_uuid() NOT NULL,
    month_routine_id uuid NOT NULL,
    previous_month_routine_id uuid,
    source_log_id uuid,
    interpretation text,
    changes jsonb,
    contraindications_added jsonb,
    raw_response jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: segmentation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segmentation (
    segmentation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    photo_id uuid NOT NULL,
    storage_bucket character varying(63) DEFAULT 'segmentations'::character varying NOT NULL,
    map_path character varying(500) NOT NULL,
    map_width integer NOT NULL,
    map_height integer NOT NULL,
    label_map jsonb NOT NULL,
    model_name character varying(50) NOT NULL,
    model_version character varying(50) NOT NULL,
    person_pixel_count integer NOT NULL,
    person_area_ratio real NOT NULL,
    detected_class_count smallint NOT NULL,
    inference_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT segmentation_detected_class_count_check CHECK ((detected_class_count >= 0)),
    CONSTRAINT segmentation_inference_ms_check CHECK ((inference_ms >= 0)),
    CONSTRAINT segmentation_map_height_check CHECK ((map_height > 0)),
    CONSTRAINT segmentation_map_width_check CHECK ((map_width > 0)),
    CONSTRAINT segmentation_person_area_ratio_check CHECK (((person_area_ratio >= (0)::double precision) AND (person_area_ratio <= (1)::double precision))),
    CONSTRAINT segmentation_person_pixel_count_check CHECK ((person_pixel_count >= 0))
);


--
-- Name: TABLE segmentation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.segmentation IS 'status 컬럼 없음 — 행의 존재 = 세그멘테이션 완료. 진행/실패는 job(kind=SEG_*)이 소스.';


--
-- Name: COLUMN segmentation.label_map; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.segmentation.label_map IS '추론 당시의 픽셀값→클래스명 매핑을 박제. {"1":"Torso",...}. 모델 버전이 바뀌면 값이 재배열됨.';


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id uuid DEFAULT gen_random_uuid() NOT NULL,
    is_pro_user boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.users IS '로그인 없음. user_id를 X-User-Id 헤더로 받아 식별한다.';


--
-- Name: workout_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workout_log (
    workout_log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    month_routine_id uuid NOT NULL,
    routine_day_id uuid NOT NULL,
    cycle_no smallint NOT NULL,
    completed_at timestamp with time zone DEFAULT now() NOT NULL,
    feedback_text text,
    CONSTRAINT workout_log_cycle_no_check CHECK (((cycle_no >= 1) AND (cycle_no <= 4)))
);


--
-- Name: COLUMN workout_log.session_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.workout_log.session_id IS '⚠️ month_routine 을 통해 유도 가능한 값이다. 소유권 검증 조인을 줄이려고 비정규화해 두었으므로, 서로 어긋나게 들어가는 것을 DB 가 막지 못한다. 기록 시 month_routine.session_id 와 일치하는지 애플리케이션이 검증한다.';


--
-- Name: analysis_session analysis_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_session
    ADD CONSTRAINT analysis_session_pkey PRIMARY KEY (session_id);


--
-- Name: body_part body_part_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_part
    ADD CONSTRAINT body_part_pkey PRIMARY KEY (class_name);


--
-- Name: body_part_segment body_part_segment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_part_segment
    ADD CONSTRAINT body_part_segment_pkey PRIMARY KEY (segment_id);


--
-- Name: body_part_segment body_part_segment_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_part_segment
    ADD CONSTRAINT body_part_segment_uniq UNIQUE (segmentation_id, class_name);


--
-- Name: exercise_catalog exercise_catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_catalog
    ADD CONSTRAINT exercise_catalog_pkey PRIMARY KEY (exercise_ref);


--
-- Name: inbody inbody_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbody
    ADD CONSTRAINT inbody_pkey PRIMARY KEY (inbody_id);


--
-- Name: inbody_segment inbody_segment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbody_segment
    ADD CONSTRAINT inbody_segment_pkey PRIMARY KEY (inbody_segment_id);


--
-- Name: inbody_segment inbody_segment_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbody_segment
    ADD CONSTRAINT inbody_segment_uniq UNIQUE (inbody_id, segment);


--
-- Name: job job_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job
    ADD CONSTRAINT job_pkey PRIMARY KEY (job_id);


--
-- Name: month_routine month_routine_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.month_routine
    ADD CONSTRAINT month_routine_pkey PRIMARY KEY (month_routine_id);


--
-- Name: month_routine month_routine_version_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.month_routine
    ADD CONSTRAINT month_routine_version_uniq UNIQUE (session_id, version);


--
-- Name: overall_diagnosis overall_diagnosis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.overall_diagnosis
    ADD CONSTRAINT overall_diagnosis_pkey PRIMARY KEY (overall_diagnosis_id);


--
-- Name: overall_diagnosis overall_diagnosis_session_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.overall_diagnosis
    ADD CONSTRAINT overall_diagnosis_session_id_key UNIQUE (session_id);


--
-- Name: part_diagnosis part_diagnosis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_diagnosis
    ADD CONSTRAINT part_diagnosis_pkey PRIMARY KEY (part_diagnosis_id);


--
-- Name: part_diagnosis part_diagnosis_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_diagnosis
    ADD CONSTRAINT part_diagnosis_uniq UNIQUE (session_id, class_name);


--
-- Name: photo photo_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photo
    ADD CONSTRAINT photo_pkey PRIMARY KEY (photo_id);


--
-- Name: photo photo_session_kind_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photo
    ADD CONSTRAINT photo_session_kind_uniq UNIQUE (session_id, kind);


--
-- Name: prompt_version prompt_version_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_version
    ADD CONSTRAINT prompt_version_pkey PRIMARY KEY (prompt_version_id);


--
-- Name: prompt_version prompt_version_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_version
    ADD CONSTRAINT prompt_version_uniq UNIQUE (name, version);


--
-- Name: routine_day_exercise routine_day_exercise_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day_exercise
    ADD CONSTRAINT routine_day_exercise_pkey PRIMARY KEY (routine_day_exercise_id);


--
-- Name: routine_day_exercise routine_day_exercise_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day_exercise
    ADD CONSTRAINT routine_day_exercise_uniq UNIQUE (routine_day_id, order_index);


--
-- Name: routine_day routine_day_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day
    ADD CONSTRAINT routine_day_pkey PRIMARY KEY (routine_day_id);


--
-- Name: routine_day routine_day_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day
    ADD CONSTRAINT routine_day_uniq UNIQUE (month_routine_id, day_order);


--
-- Name: routine_revision routine_revision_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_revision
    ADD CONSTRAINT routine_revision_pkey PRIMARY KEY (routine_revision_id);


--
-- Name: segmentation segmentation_photo_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segmentation
    ADD CONSTRAINT segmentation_photo_id_key UNIQUE (photo_id);


--
-- Name: segmentation segmentation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segmentation
    ADD CONSTRAINT segmentation_pkey PRIMARY KEY (segmentation_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: workout_log workout_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_log
    ADD CONSTRAINT workout_log_pkey PRIMARY KEY (workout_log_id);


--
-- Name: workout_log workout_log_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_log
    ADD CONSTRAINT workout_log_uniq UNIQUE (month_routine_id, cycle_no, routine_day_id);


--
-- Name: analysis_session_one_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX analysis_session_one_active_idx ON public.analysis_session USING btree (user_id) WHERE ((status)::text = 'ACTIVE'::text);


--
-- Name: analysis_session_user_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX analysis_session_user_created_idx ON public.analysis_session USING btree (user_id, created_at DESC);


--
-- Name: body_part_segment_valid_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX body_part_segment_valid_idx ON public.body_part_segment USING btree (segmentation_id) WHERE is_valid;


--
-- Name: exercise_catalog_type_safe_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX exercise_catalog_type_safe_idx ON public.exercise_catalog USING btree (exercise_type, is_beginner_safe);


--
-- Name: inbody_session_measured_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX inbody_session_measured_idx ON public.inbody USING btree (session_id, measured_at DESC NULLS LAST);


--
-- Name: job_open_one_per_kind_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX job_open_one_per_kind_idx ON public.job USING btree (session_id, kind) WHERE ((status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying])::text[]));


--
-- Name: job_poll_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX job_poll_idx ON public.job USING btree (status, kind, created_at);


--
-- Name: job_session_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX job_session_idx ON public.job USING btree (session_id);


--
-- Name: month_routine_one_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX month_routine_one_active_idx ON public.month_routine USING btree (session_id) WHERE is_active;


--
-- Name: prompt_version_one_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX prompt_version_one_active_idx ON public.prompt_version USING btree (name) WHERE is_active;


--
-- Name: workout_log_routine_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX workout_log_routine_idx ON public.workout_log USING btree (month_routine_id, cycle_no);


--
-- Name: analysis_session analysis_session_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_session
    ADD CONSTRAINT analysis_session_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: body_part_segment body_part_segment_class_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_part_segment
    ADD CONSTRAINT body_part_segment_class_name_fkey FOREIGN KEY (class_name) REFERENCES public.body_part(class_name);


--
-- Name: body_part_segment body_part_segment_segmentation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_part_segment
    ADD CONSTRAINT body_part_segment_segmentation_id_fkey FOREIGN KEY (segmentation_id) REFERENCES public.segmentation(segmentation_id) ON DELETE CASCADE;


--
-- Name: inbody_segment inbody_segment_inbody_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbody_segment
    ADD CONSTRAINT inbody_segment_inbody_id_fkey FOREIGN KEY (inbody_id) REFERENCES public.inbody(inbody_id) ON DELETE CASCADE;


--
-- Name: inbody inbody_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inbody
    ADD CONSTRAINT inbody_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.analysis_session(session_id) ON DELETE CASCADE;


--
-- Name: job job_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job
    ADD CONSTRAINT job_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.analysis_session(session_id) ON DELETE CASCADE;


--
-- Name: month_routine month_routine_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.month_routine
    ADD CONSTRAINT month_routine_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.analysis_session(session_id) ON DELETE CASCADE;


--
-- Name: overall_diagnosis overall_diagnosis_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.overall_diagnosis
    ADD CONSTRAINT overall_diagnosis_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.analysis_session(session_id) ON DELETE CASCADE;


--
-- Name: part_diagnosis part_diagnosis_class_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_diagnosis
    ADD CONSTRAINT part_diagnosis_class_name_fkey FOREIGN KEY (class_name) REFERENCES public.body_part(class_name);


--
-- Name: part_diagnosis part_diagnosis_reference_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_diagnosis
    ADD CONSTRAINT part_diagnosis_reference_segment_id_fkey FOREIGN KEY (reference_segment_id) REFERENCES public.body_part_segment(segment_id) ON DELETE SET NULL;


--
-- Name: part_diagnosis part_diagnosis_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_diagnosis
    ADD CONSTRAINT part_diagnosis_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.analysis_session(session_id) ON DELETE CASCADE;


--
-- Name: part_diagnosis part_diagnosis_user_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.part_diagnosis
    ADD CONSTRAINT part_diagnosis_user_segment_id_fkey FOREIGN KEY (user_segment_id) REFERENCES public.body_part_segment(segment_id) ON DELETE SET NULL;


--
-- Name: photo photo_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photo
    ADD CONSTRAINT photo_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.analysis_session(session_id) ON DELETE CASCADE;


--
-- Name: routine_day_exercise routine_day_exercise_boosted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day_exercise
    ADD CONSTRAINT routine_day_exercise_boosted_by_fkey FOREIGN KEY (boosted_by) REFERENCES public.body_part(class_name);


--
-- Name: routine_day_exercise routine_day_exercise_exercise_ref_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day_exercise
    ADD CONSTRAINT routine_day_exercise_exercise_ref_fkey FOREIGN KEY (exercise_ref) REFERENCES public.exercise_catalog(exercise_ref);


--
-- Name: routine_day_exercise routine_day_exercise_routine_day_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day_exercise
    ADD CONSTRAINT routine_day_exercise_routine_day_id_fkey FOREIGN KEY (routine_day_id) REFERENCES public.routine_day(routine_day_id) ON DELETE CASCADE;


--
-- Name: routine_day routine_day_month_routine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_day
    ADD CONSTRAINT routine_day_month_routine_id_fkey FOREIGN KEY (month_routine_id) REFERENCES public.month_routine(month_routine_id) ON DELETE CASCADE;


--
-- Name: routine_revision routine_revision_month_routine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_revision
    ADD CONSTRAINT routine_revision_month_routine_id_fkey FOREIGN KEY (month_routine_id) REFERENCES public.month_routine(month_routine_id) ON DELETE CASCADE;


--
-- Name: routine_revision routine_revision_previous_month_routine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_revision
    ADD CONSTRAINT routine_revision_previous_month_routine_id_fkey FOREIGN KEY (previous_month_routine_id) REFERENCES public.month_routine(month_routine_id) ON DELETE SET NULL;


--
-- Name: routine_revision routine_revision_source_log_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routine_revision
    ADD CONSTRAINT routine_revision_source_log_id_fkey FOREIGN KEY (source_log_id) REFERENCES public.workout_log(workout_log_id) ON DELETE SET NULL;


--
-- Name: segmentation segmentation_photo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segmentation
    ADD CONSTRAINT segmentation_photo_id_fkey FOREIGN KEY (photo_id) REFERENCES public.photo(photo_id) ON DELETE CASCADE;


--
-- Name: workout_log workout_log_month_routine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_log
    ADD CONSTRAINT workout_log_month_routine_id_fkey FOREIGN KEY (month_routine_id) REFERENCES public.month_routine(month_routine_id) ON DELETE CASCADE;


--
-- Name: workout_log workout_log_routine_day_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_log
    ADD CONSTRAINT workout_log_routine_day_id_fkey FOREIGN KEY (routine_day_id) REFERENCES public.routine_day(routine_day_id) ON DELETE CASCADE;


--
-- Name: workout_log workout_log_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_log
    ADD CONSTRAINT workout_log_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.analysis_session(session_id) ON DELETE CASCADE;


--
-- Name: analysis_session; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.analysis_session ENABLE ROW LEVEL SECURITY;

--
-- Name: body_part; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.body_part ENABLE ROW LEVEL SECURITY;

--
-- Name: body_part_segment; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.body_part_segment ENABLE ROW LEVEL SECURITY;

--
-- Name: exercise_catalog; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.exercise_catalog ENABLE ROW LEVEL SECURITY;

--
-- Name: inbody; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inbody ENABLE ROW LEVEL SECURITY;

--
-- Name: inbody_segment; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inbody_segment ENABLE ROW LEVEL SECURITY;

--
-- Name: job; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.job ENABLE ROW LEVEL SECURITY;

--
-- Name: month_routine; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.month_routine ENABLE ROW LEVEL SECURITY;

--
-- Name: overall_diagnosis; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.overall_diagnosis ENABLE ROW LEVEL SECURITY;

--
-- Name: part_diagnosis; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.part_diagnosis ENABLE ROW LEVEL SECURITY;

--
-- Name: photo; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.photo ENABLE ROW LEVEL SECURITY;

--
-- Name: prompt_version; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.prompt_version ENABLE ROW LEVEL SECURITY;

--
-- Name: routine_day; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.routine_day ENABLE ROW LEVEL SECURITY;

--
-- Name: routine_day_exercise; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.routine_day_exercise ENABLE ROW LEVEL SECURITY;

--
-- Name: routine_revision; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.routine_revision ENABLE ROW LEVEL SECURITY;

--
-- Name: segmentation; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.segmentation ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- Name: workout_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.workout_log ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--
