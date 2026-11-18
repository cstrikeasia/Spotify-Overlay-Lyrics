"""Desktop UI translations; locale comes from the operating system by default.

Chinese (Traditional) is the canonical key language. Unknown locale values
fall back to English, while missing translations fall back to the key itself.
No network access or additional language/font packages are required.
"""
from __future__ import annotations

import locale

LANGUAGES = (
    ("system", "System / 系統預設"),
    ("zh_TW", "繁體中文"),
    ("zh_CN", "简体中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
)

_active = "zh_TW"


def detect_system_language(locale_name: str | None = None) -> str:
    """Resolve QLocale-style OS languages, including regional Chinese scripts."""
    if locale_name is None:
        try:
            from PySide6.QtCore import QLocale
            locale_name = QLocale.system().name()
        except ImportError:
            value = locale.getlocale()[0]
            locale_name = value or "en"
    name = str(locale_name or "en").replace("-", "_").lower()
    if name.startswith("zh"):
        return "zh_CN" if ("cn" in name or "sg" in name or "hans" in name) else "zh_TW"
    if name.startswith("ja"):
        return "ja"
    if name.startswith("ko"):
        return "ko"
    return "en"


def set_language(code: str, system_locale: str | None = None) -> str:
    global _active
    _active = detect_system_language(system_locale) if code == "system" else code
    if _active not in {"zh_TW", "zh_CN", "en", "ja", "ko"}:
        _active = "en"
    return _active


def current_language() -> str:
    return _active


# key: (简体中文, English, 日本語, 한국어)
_TEXT: dict[str, tuple[str, str, str, str]] = {
    '歌詞文字': ('歌词文字', 'Lyric characters', '歌詞の文字', '가사 문자'),
    '與介面語言獨立；不會修改下載的原始 LRC 或同步時間。': ('独立于界面语言；不会修改下载的原始 LRC 或同步时间。', 'Independent of UI language; original LRC and timing stay intact.', 'UI 言語とは独立。元の LRC と同期時間は変更しません。', 'UI 언어와 독립적이며 원본 LRC와 타임코드는 유지됩니다.'),
    '自動（依歌曲名稱判斷）': ('自动（根据歌曲名称判断）', 'Automatic (song metadata)', '自動（曲名で判定）', '자동 (곡 제목 기준)'),
    '強制繁體中文': ('强制繁体中文', 'Traditional Chinese', '繁体字に固定', '번체 중국어'),
    '強制簡體中文': ('强制简体中文', 'Simplified Chinese', '簡体字に固定', '간체 중국어'),
    '保留來源原文': ('保留来源原文', 'Keep original', '元の表記を維持', '원본 유지'),
    '歌詞繁簡設定': ('歌词简繁设置', 'Lyrics script', '歌詞の字体', '가사 문자 체계'),
    '所有歌詞視窗、靈動島與 OBS 同步套用；切換不必重新搜尋。': ('应用于所有歌词窗口、灵动岛及 OBS；切换无需重新搜索。', 'Applies to player, island, and OBS without a new search.', 'プレーヤー、Dynamic Island、OBS に適用。再検索不要。', '플레이어, 다이내믹 아일랜드, OBS에 적용. 재검색 불필요.'),
    "外觀": ("外观", "Appearance", "外観", "모양"),
    "桌面靈動島": ("桌面灵动岛", "Desktop Dynamic Island", "デスクトップ Dynamic Island", "데스크톱 다이내믹 아일랜드"),
    "在螢幕頂端顯示迷你播放器，不影響原本的浮動歌詞與 OBS。": ("在屏幕顶部显示迷你播放器，不影响原有的悬浮歌词和 OBS。", "Show a mini-player at the top of the screen without affecting lyrics or OBS.", "画面上部にミニプレイヤーを表示。歌詞オーバーレイや OBS とは独立しています。", "화면 상단에 미니 플레이어를 표시하며 가사 오버레이 및 OBS에는 영향이 없습니다."),
    "啟用桌面靈動島": ("启用桌面灵动岛", "Enable Dynamic Island", "Dynamic Island を有効化", "다이내믹 아일랜드 활성화"),
    "滑鼠移入時自動展開": ("鼠标移入时自动展开", "Expand on hover", "マウスを乗せると展開", "마우스를 올리면 자동 확장"),
    "展開後顯示同步歌詞": ("展开后显示同步歌词", "Show synced lyrics when expanded", "展開時に同期歌詞を表示", "확장 시 동기화 가사 표시"),
    "在靈動島顯示同步歌詞": ("在灵动岛显示同步歌词", "Show synced lyrics in the Island", "Dynamic Island に同期歌詞を表示", "다이내믹 아일랜드에 동기화 가사 표시"),
    "固定於主螢幕頂端中央，不可拖曳。收合時顯示封面、歌手 - 歌名與當前歌詞；點擊展開，右鍵可進入設定或關閉。": (
        "固定在主屏幕顶部中央，无法拖动。收起时显示封面、歌手 - 歌名和当前歌词；点击展开，右键进入设置或关闭。",
        "Fixed at the top-center of the primary screen; dragging is disabled. The collapsed pill shows cover art, artist - song title and live lyrics. Click to expand; right-click for settings.",
        "メイン画面の上部中央に固定され、ドラッグ移動できません。収納時はジャケット、アーティスト - 曲名、現在の歌詞を表示。クリックで展開、右クリックで設定します。",
        "주 모니터 상단 중앙에 고정되며 드래그할 수 없습니다. 축소 시 커버와 아티스트 - 곡명 및 현재 가사를 표시하며 클릭하여 펼치고 우클릭으로 설정합니다.",
    ),
    "純音樂，無同步歌詞": ("纯音乐，无同步歌词", "Instrumental · no synced lyrics", "インストゥルメンタル · 同期歌詞なし", "연주곡 · 동기화 가사 없음"),
    "目前沒有同步歌詞": ("目前没有同步歌词", "No synced lyrics available", "同期歌詞はありません", "동기화 가사가 없습니다"),
    "歌詞暫時無法載入": ("歌词暂时无法加载", "Lyrics temporarily unavailable", "歌詞を読み込めません", "가사를 불러올 수 없습니다"),
    "顯示主視窗": ("显示主窗口", "Show main window", "メイン画面を表示", "메인 창 보기"),
    "固定展開靈動島": ("固定展开灵动岛", "Keep Island expanded", "展開状態に固定", "확장 상태 고정"),
    "關閉桌面靈動島": ("关闭桌面灵动岛", "Disable Dynamic Island", "Dynamic Island を無効化", "다이내믹 아일랜드 비활성화"),
    "收合靈動島": ("收起灵动岛", "Collapse Island", "折りたたむ", "아일랜드 접기"),
    "正在播放": ("正在播放", "NOW PLAYING", "再生中", "재생 중"),
    "播放後自動顯示歌曲資訊": ("播放后自动显示歌曲信息", "Song details appear here", "再生すると曲名を表示します", "재생 시 곡 정보가 표시됩니다"),
    "請先開啟 Spotify 桌面版": ("请先打开 Spotify 桌面版", "Launch the Spotify desktop app", "Spotify デスクトップアプリを起動してください", "Spotify 데스크톱 앱을 실행하세요"),
    "等待播放音樂": ("等待播放音乐", "Waiting for music", "音楽の再生待ち", "음악 재생 대기"),
    "歌詞": ("歌词", "Lyrics", "歌詞", "가사"),
    "系統": ("系统", "System", "システム", "시스템"),
    "設定": ("设置", "Settings", "設定", "설정"),
    "設定 · Spotify Lyrics": ("设置 · Spotify Lyrics", "Settings · Spotify Lyrics", "設定 · Spotify Lyrics", "설정 · Spotify Lyrics"),
    "設定懸浮播放器的版面與顏色": ("调整悬浮播放器的布局与颜色", "Customize the overlay player layout and colors", "フローティングプレイヤーの表示と色", "플로팅 플레이어 레이아웃 및 색상"),
    "同步顯示與轉場速度": ("同步显示与过渡速度", "Timing and lyric transitions", "同期表示と切り替え速度", "동기화 및 전환 속도"),
    "OBS 整合": ("OBS 集成", "OBS integration", "OBS 連携", "OBS 연동"),
    "管理直播與錄影的歌詞輸出": ("管理直播与录制的歌词输出", "Manage lyrics in OBS scenes", "OBS への歌詞表示を設定", "OBS 가사 출력 관리"),
    "視窗層級與開機啟動": ("窗口置顶与开机启动", "Window behavior and startup", "ウィンドウと自動起動", "창 및 시작 설정"),
    "設定選單": ("设置菜单", "PREFERENCES", "設定メニュー", "설정 메뉴"),
    "本機偏好設定\n非 Spotify 官方應用程式": ("本地偏好设置\n非 Spotify 官方应用", "Local preferences\nUnofficial Spotify companion", "ローカル設定\n非公式 Spotify アプリ", "로컬 설정\n비공식 Spotify 앱"),
    "關閉設定": ("关闭设置", "Close settings", "設定を閉じる", "설정 닫기"),
    "變更將儲存至這台電腦": ("更改将保存到这台电脑", "Changes are saved on this PC", "変更はこの PC に保存されます", "변경 사항은 이 PC에 저장됩니다"),
    "取消": ("取消", "Cancel", "キャンセル", "취소"),
    "儲存設定": ("保存设置", "Save settings", "設定を保存", "설정 저장"),
    "拖曳以調整設定視窗大小": ("拖动调整设置窗口大小", "Drag to resize settings", "ドラッグして設定画面をサイズ変更", "끌어서 설정 창 크기 조절"),
    "桌面模式": ("桌面模式", "Desktop mode", "デスクトップモード", "데스크톱 모드"),
    "完整視窗顯示專輯封面": ("完整窗口显示专辑封面", "Show album art in the full player", "プレイヤーにアルバム画像を表示", "전체 창에 앨범 표지 표시"),
    "在應用模式與工具列的浮動模式之間切換。": ("通过工具栏在应用模式和浮动模式间切换。", "Switch between app and overlay lyric modes from the toolbar.", "ツールバーからアプリ表示とフローティング表示を切り替えます。", "툴바에서 앱 모드와 플로팅 가사 모드를 전환하세요."),
    "依專輯封面自動配色歌詞背景": ("根据专辑封面自动调整歌词背景色", "Match lyric background to the album art", "アルバム画像に合わせて歌詞背景を配色", "앨범 표지에 맞춰 가사 배경색 변경"),
    "每首歌曲使用不同深色調，並維持白色歌詞可讀性": ("每首歌曲使用不同的深色调，保持白色歌词清晰", "Per-song dark colors while preserving legible white lyrics", "曲ごとに背景を変え、白い歌詞の読みやすさを維持", "곡마다 어두운 배경색을 적용해 가사 가독성 유지"),
    "視覺調整": ("视觉调整", "Visual settings", "表示設定", "시각 설정"),
    "歌詞字體大小": ("歌词字体大小", "Lyric font size", "歌詞の文字サイズ", "가사 글꼴 크기"),
    "桌面與 OBS 歌詞的主要字級": ("桌面与 OBS 歌词的主要字号", "Text size for desktop and OBS lyrics", "デスクトップと OBS の文字サイズ", "데스크톱 및 OBS 가사 글꼴 크기"),
    "更換顏色": ("更换颜色", "Choose color", "色を変更", "색상 변경"),
    "純文字歌詞顏色": ("纯文字歌词颜色", "Overlay lyric text color", "フローティング歌詞の文字色", "플로팅 가사 글자색"),
    "透明背景模式的文字色彩": ("透明背景模式的文字颜色", "Text color in transparent overlay mode", "透明表示の文字色", "투명 모드의 글자색"),
    '應用視窗透明度': ('应用窗口透明度', 'App window opacity', 'アプリウィンドウの不透明度', '앱 창 불투명도'),
    '應用外框透明度': ('应用外框透明度', 'App frame opacity', 'アプリ外枠の不透明度', '앱 외곽 배경 불투명도'),
    '只調整外框背景；歌曲、歌詞與按鈕保持不透明': ('仅调整外框背景；歌曲、歌词与按钮保持不透明', 'Only the outer frame fades; track, lyrics and buttons stay opaque', '外枠の背景のみ透過し、曲情報・歌詞・ボタンは不透明のままです', '바깥 배경만 투명하게 조정하며 곡 정보·가사·버튼은 불투명하게 유지됩니다'),
    '只調整完整播放器，不影響浮動歌詞': ('只调整完整播放器，不影响浮动歌词', 'Affects only the full player, not overlay lyrics', 'アプリ表示のみに適用、歌詞オーバーレイには影響しません', '전체 플레이어에만 적용됨'),
    '浮動歌詞透明度': ('浮动歌词透明度', 'Overlay lyrics opacity', 'フローティング歌詞の不透明度', '플로팅 가사 불투명도'),
    '只調整純文字浮動模式，不影響應用視窗': ('只调整纯文字浮动模式，不影响应用窗口', 'Affects only overlay text, not the full player', '歌詞オーバーレイのみに適用', '플로팅 가사에만 적용됨'),
    '兩種模式的位置與透明度分開儲存，拖曳後會自動記住': ('两种模式的位置与透明度分开保存，拖动后自动记住', 'Each mode remembers its own position and opacity automatically', '表示モードごとに位置と不透明度を記憶します', '모드별로 위치와 불투명도를 기억합니다'),
    "視窗不透明度": ("窗口不透明度", "Window opacity", "ウィンドウの不透明度", "창 불투명도"),
    "調整懸浮視窗的背景強度": ("调整悬浮窗口背景深浅", "Adjust the overlay window background opacity", "フローティング背景の濃さ", "플로팅 창 배경 불투명도"),
    "時間軸設定": ("时间轴设置", "Synchronization", "同期設定", "싱크 설정"),
    "使畫面上的字句與音樂節拍維持一致。": ("让歌词与音乐节奏保持同步。", "Keep displayed lyrics aligned with the music.", "歌詞を楽曲に同期させます。", "가사를 음악에 동기화합니다."),
    "時間補償": ("时间偏移", "Timing offset", "タイミング調整", "시간 오프셋"),
    "正值提前顯示，負值延後顯示；預設 +300 ms": ("正值提前显示，负值延后；默认 +300 ms", "Positive shows earlier; negative shows later (+300 ms by default)", "正の値で先行・負の値で遅延（既定 +300 ms）", "양수는 빨리, 음수는 늦게 표시 (기본 +300 ms)"),
    "3 行 · 精簡": ("3 行 · 精简", "3 lines · compact", "3 行 · コンパクト", "3줄 · 간략"),
    "5 行 · 完整": ("5 行 · 完整", "5 lines · full", "5 行 · フル", "5줄 · 전체"),
    "同步歌詞行數": ("同步歌词行数", "Visible lyric lines", "歌詞の表示行数", "가사 표시 줄 수"),
    "拖曳進度條調整播放位置": ("拖动进度条调整播放位置", "Drag the seek bar to change playback position", "シークバーをドラッグして再生位置を変更", "진행 막대를 드래그하여 재생 위치 이동"),
    "純文字模式固定只顯示目前一句": ("纯文字模式只显示当前一句", "Overlay text mode shows the active line", "透明表示では現在の行のみ表示", "플로팅 모드에서는 현재 가사만 표시"),
    "歌詞動畫": ("歌词动画", "Lyric animation", "歌詞アニメーション", "가사 애니메이션"),
    "完整應用模式固定靜態切換；純文字浮動模式可使用平滑動畫。": ("应用模式不使用动画；浮动模式可使用平滑过渡。", "App mode has no animation; overlay mode can animate.", "アプリ表示は静止、フローティング表示のみアニメーション可能です。", "앱 모드는 애니메이션 없음, 플로팅 모드만 부드럽게 전환됩니다."),
    "浮動歌詞及 OBS 啟用平滑切換": ("浮动歌词与 OBS 启用平滑过渡", "Smooth overlay lyrics and OBS transitions", "フローティング歌詞と OBS で滑らかに切り替え", "플로팅 가사 및 OBS 부드러운 전환"),
    "快速 · 380 ms": ("快速 · 380 ms", "Fast · 380 ms", "高速 · 380 ms", "빠름 · 380 ms"),
    "標準 · 620 ms": ("标准 · 620 ms", "Normal · 620 ms", "標準 · 620 ms", "보통 · 620 ms"),
    "柔順 · 850 ms": ("柔顺 · 850 ms", "Smooth · 850 ms", "ゆっくり · 850 ms", "부드러움 · 850 ms"),
    "歌詞切換速度": ("歌词切换速度", "Transition speed", "切り替え速度", "전환 속도"),
    "僅影響純文字浮動歌詞與 OBS，不影響完整應用模式": ("仅影响浮动歌词和 OBS，不影响应用模式", "Only affects overlay lyrics and OBS, not app mode", "フローティング歌詞と OBS のみに適用", "플로팅 가사 및 OBS에만 적용"),
    "浮動歌詞顯示效果": ("浮动歌词显示效果", "Overlay lyric effects", "フローティング歌詞の表示効果", "플로팅 가사 표시 효과"),
    "只影響浮動歌詞；完整應用模式固定不使用動畫。": ("仅影响浮动歌词；完整应用模式始终不使用动画。", "Only overlay lyrics are animated; app mode always stays static.", "フローティング歌詞のみに適用。アプリ表示では常にアニメーションなし。", "플로팅 가사에만 적용되며 앱 모드는 항상 애니메이션이 없습니다."),
    "歌詞顯示方式": ("歌词显示方式", "Display effect", "表示方法", "가사 표시 방식"),
    "無動畫": ("无动画", "No animation", "アニメーションなし", "애니메이션 없음"),
    "動態滾動": ("动态滚动", "Smooth scroll", "スムーズスクロール", "부드러운 스크롤"),
    "淡出淡入": ("淡出淡入", "Fade out / in", "フェードアウト・イン", "페이드 아웃 / 인"),
    "無動畫立即切換；動態滾動平滑移動；淡出淡入原地交替顯示": ("无动画立即切换；动态滚动平滑移动；淡出淡入原地切换", "Instant switch, smooth vertical scroll, or fade in place", "即時切替・滑らかなスクロール・その場でフェード切替", "즉시 전환·부드러운 스크롤·제자리 페이드 전환"),
    "調整浮動歌詞動畫與 OBS 平滑切換速度；無動畫模式不受影響": ("调整浮动歌词动画及 OBS 平滑切换速度；无动画模式不受影响", "Controls overlay lyric effects and OBS transition speed; no effect in static mode", "フローティング歌詞と OBS のアニメーション速度を設定します", "플로팅 가사 및 OBS 전환 속도 설정; 애니메이션 없음 모드는 제외"),
    "OBS 歌詞動畫": ("OBS 歌词动画", "OBS lyric animation", "OBS 歌詞アニメーション", "OBS 가사 애니메이션"),
    "OBS 的平滑切換獨立於浮動歌詞顯示方式。": ("OBS 平滑切换与浮动歌词显示方式互不影响。", "OBS smooth transitions are independent of overlay lyric effects.", "OBS の滑らかな切替はフローティング歌詞と独立して設定できます。", "OBS 부드러운 전환은 플로팅 가사 효과와 별도로 설정합니다."),
    "OBS 歌詞啟用平滑切換": ("启用 OBS 歌词平滑切换", "Enable smooth OBS lyric transitions", "OBS 歌詞を滑らかに切り替える", "OBS 가사 부드러운 전환 사용"),
    "瀏覽器來源": ("浏览器来源", "Browser source", "ブラウザーソース", "브라우저 소스"),
    "將下方網址新增為 OBS 的「瀏覽器」來源。": ("将下方网址添加为 OBS 的浏览器来源。", "Add this URL as an OBS browser source.", "この URL を OBS のブラウザーソースに追加します。", "이 URL을 OBS 브라우저 소스로 추가하세요."),
    "啟用 OBS 瀏覽器來源": ("启用 OBS 浏览器来源", "Enable OBS browser source", "OBS ブラウザーソースを有効化", "OBS 브라우저 소스 사용"),
    "在 OBS 畫面顯示專輯封面": ("在 OBS 中显示专辑封面", "Show album art in OBS", "OBS にアルバム画像を表示", "OBS에 앨범 표지 표시"),
    "本機服務連接埠": ("本地服务端口", "Local server port", "ローカルポート", "로컬 서버 포트"),
    "複製": ("复制", "Copy", "コピー", "복사"),
    "擷取相容性": ("采集兼容性", "Capture compatibility", "キャプチャ互換性", "캡처 호환성"),
    "啟用 OBS 視窗擷取相容模式": ("启用 OBS 窗口采集兼容模式", "Enable OBS window capture compatibility", "OBS ウィンドウキャプチャ互換モード", "OBS 창 캡처 호환성 사용"),
    "可使用 ?cover=1 顯示封面、?cover=0 隱藏封面，或 ?track=0&cover=0 輸出純歌詞。": ("使用 ?cover=1 显示封面、?cover=0 隐藏，或 ?track=0&cover=0 仅输出歌词。", "Use ?cover=1 to show artwork, ?cover=0 to hide it, or ?track=0&cover=0 for lyrics only.", "?cover=1 は画像表示、?cover=0 は非表示、?track=0&cover=0 は歌詞のみ。", "?cover=1 표지 표시, ?cover=0 숨기기, ?track=0&cover=0 가사만 출력."),
    "視窗行為": ("窗口行为", "Window behavior", "ウィンドウ動作", "창 동작"),
    "設定視窗與 Windows 桌面的互動方式。": ("设置窗口在 Windows 桌面的行为。", "Configure how this window interacts with Windows.", "Windows 上の動作を設定します。", "Windows에서 창 동작을 설정합니다."),
    "讓懸浮歌詞始終顯示在其他視窗上方": ("让悬浮歌词始终置顶", "Keep overlay lyrics always on top", "フローティング歌詞を最前面に表示", "플로팅 가사를 항상 위에 표시"),
    "遊戲浮動模式（置頂、滑鼠穿透）": ("游戏浮动模式（置顶、鼠标穿透）", "Game overlay (always on top, click-through)", "ゲームオーバーレイ（最前面・クリック透過）", "게임 오버레이 (항상 위, 클릭 통과)"),
    "適用於視窗化與無邊框全螢幕遊戲；透過系統匣可隨時關閉並移動歌詞。獨佔全螢幕不保證顯示。": ("适用于窗口化及无边框全屏游戏；可以从系统托盘关闭并移动歌词。独占全屏无法保证显示。", "Works with windowed and borderless fullscreen games. Turn it off from the system tray to move lyrics. Exclusive fullscreen is not guaranteed.", "ウィンドウ・ボーダーレス全画面ゲーム向け。トレイから解除して位置を変更できます。排他的全画面では表示を保証できません。", "창 모드 및 테두리 없는 전체 화면 게임에서 사용합니다. 트레이에서 해제한 뒤 가사를 이동하세요. 독점 전체 화면은 지원되지 않을 수 있습니다."),
    "遊戲浮動模式": ("游戏浮动模式", "Game overlay", "ゲームオーバーレイ", "게임 오버레이"),
    "遊戲模式需要 Windows 系統匣，才能安全解除滑鼠穿透。": ("游戏模式需要 Windows 系统托盘，以便安全解除鼠标穿透。", "Game overlay requires a Windows system tray so click-through can always be disabled.", "クリック透過を安全に解除するには Windows の通知領域が必要です。", "클릭 통과를 안전하게 해제하려면 Windows 시스템 트레이가 필요합니다."),
    "歌詞將置頂且不阻擋滑鼠。從系統匣取消遊戲模式即可拖曳位置。": ("歌词将置顶且不阻挡鼠标。从系统托盘关闭游戏模式后可拖动位置。", "Lyrics stay on top without blocking clicks. Turn off Game overlay from the tray to move them.", "歌詞は最前面でクリックを遮りません。移動するにはトレイからゲームモードを解除してください。", "가사가 항상 위에 표시되며 클릭을 막지 않습니다. 이동하려면 트레이에서 게임 모드를 해제하세요."),
    "登入 Windows 時自動啟動": ("登录 Windows 时自动启动", "Start automatically with Windows", "Windows ログイン時に自動起動", "Windows 로그인 시 자동 시작"),
    "語言": ("语言", "Language", "言語", "언어"),
    "介面語言": ("界面语言", "Interface language", "表示言語", "인터페이스 언어"),
    "預設跟隨作業系統，修改後立即套用": ("默认跟随系统语言，更改后立即生效", "Follows your system language by default; changes apply immediately", "既定はシステム言語。変更はすぐ反映されます。", "기본값은 시스템 언어이며 변경 즉시 적용됩니다"),
    "系統預設": ("系统默认", "System default", "システム既定", "시스템 기본값"),
    "關於本應用": ("关于本应用", "About", "このアプリについて", "앱 정보"),
    "從 Windows 媒體工作階段取得 Spotify 播放資訊，透過公開歌詞來源提供同步顯示。不需要 Spotify 登入授權。\n\n設定儲存於目前 Windows 使用者的本機資料夾。": (
        "从 Windows 媒体会话读取 Spotify 播放状态并显示同步歌词，无需 Spotify 登录授权。\n\n设置保存在当前 Windows 用户的本地文件夹。",
        "Reads Spotify playback from Windows media sessions and displays synchronized lyrics. No Spotify login is required.\n\nPreferences are stored locally for the current Windows user.",
        "Windows メディアセッションから再生情報を取得し、同期歌詞を表示します。Spotify へのログイン認証は不要です。\n\n設定はこの Windows ユーザーに保存されます。",
        "Windows 미디어 세션에서 Spotify 재생 정보를 읽고 동기화된 가사를 표시합니다. Spotify 로그인은 필요하지 않습니다.\n\n설정은 현재 Windows 사용자 폴더에 저장됩니다."
    ),
    "選擇歌詞文字顏色": ("选择歌词文字颜色", "Choose lyric text color", "歌詞の文字色を選択", "가사 글자색 선택"),
    "目前的 Spotify 專輯封面": ("当前 Spotify 专辑封面", "Current Spotify album art", "現在のアルバム画像", "현재 Spotify 앨범 표지"),
    "按住這裡拖曳懸浮視窗": ("拖动此处移动悬浮窗口", "Drag here to move the overlay window", "ここをドラッグして移動", "여기를 끌어 창 이동"),
    "切換純文字懸浮模式": ("切换纯文字悬浮模式", "Toggle overlay lyric mode", "フローティング歌詞モードを切り替え", "플로팅 가사 모드 전환"),
    "重新搜尋同步歌詞": ("重新搜索同步歌词", "Search lyrics again", "歌詞を再検索", "가사 다시 검색"),
    "開啟設定": ("打开设置", "Open settings", "設定を開く", "설정 열기"),
    "最小化至系統匣": ("最小化到系统托盘", "Minimize to tray", "トレイに最小化", "트레이로 최소화"),
    "結束應用程式": ("退出应用程序", "Exit app", "アプリを終了", "앱 종료"),
    "等待 Spotify 播放": ("等待 Spotify 播放", "Waiting for Spotify", "Spotify の再生待ち", "Spotify 재생 대기"),
    "Spotify 播放歌曲後自動更新": ("Spotify 播放歌曲后自动更新", "Updates when Spotify plays a song", "Spotify の再生に合わせて更新", "Spotify 재생 시 자동 업데이트"),
    "啟動 Spotify，播放你喜歡的音樂": ("打开 Spotify 并播放音乐", "Open Spotify and start listening", "Spotify を開いて音楽を再生", "Spotify를 열고 음악을 재생하세요"),
    "啟動 Spotify 並播放歌曲": ("打开 Spotify 并播放歌曲", "Open Spotify and play a song", "Spotify で楽曲を再生してください", "Spotify에서 노래를 재생하세요"),
    "拖曳這個角落調整視窗大小": ("拖动角落调整窗口大小", "Drag the corner to resize", "角をドラッグしてサイズ変更", "모서리를 끌어 창 크기 조절"),
    "此系統目前沒有可用的系統匣": ("系统托盘不可用", "System tray unavailable", "通知領域が利用できません", "시스템 트레이를 사용할 수 없음"),
    "顯示懸浮歌詞": ("显示悬浮歌词", "Show lyrics window", "歌詞ウィンドウを表示", "가사 창 표시"),
    "純文字模式（無背景）": ("纯文字模式（无背景）", "Overlay lyrics (no background)", "フローティング歌詞（背景なし）", "플로팅 가사 (배경 없음)"),
    "更換歌詞顏色…": ("更换歌词颜色…", "Change lyric color…", "歌詞の色を変更…", "가사 색 변경…"),
    "滑鼠穿透（由系統匣解除）": ("鼠标穿透（通过系统托盘关闭）", "Click-through (turn off from tray)", "マウス操作を透過（トレイから解除）", "마우스 클릭 통과 (트레이에서 해제)"),
    "永遠置頂": ("始终置顶", "Always on top", "常に最前面", "항상 위에 표시"),
    "OBS 視窗擷取相容模式": ("OBS 窗口采集兼容模式", "OBS window capture compatibility", "OBS ウィンドウキャプチャ互換", "OBS 창 캡처 호환성"),
    "複製 OBS 瀏覽器來源網址": ("复制 OBS 浏览器来源网址", "Copy OBS browser source URL", "OBS ブラウザー URL をコピー", "OBS 브라우저 소스 URL 복사"),
    "重新搜尋歌詞": ("重新搜索歌词", "Refresh lyrics", "歌詞を更新", "가사 새로고침"),
    "結束 Spotify 浮動歌詞": ("退出 Spotify 悬浮歌词", "Quit Spotify Lyrics", "Spotify Lyrics を終了", "Spotify Lyrics 종료"),
    "更換歌詞顏色": ("更换歌词颜色", "Change lyric color", "歌詞色を変更", "가사 색 변경"),
    "切回完整視窗": ("切回完整窗口", "Back to app mode", "アプリ表示に戻る", "앱 모드로 돌아가기"),
    "歌詞顏色": ("歌词颜色", "Lyric color", "歌詞の色", "가사 색상"),
    "白色": ("白色", "White", "白", "흰색"),
    "Spotify 綠": ("Spotify 绿", "Spotify green", "Spotify グリーン", "Spotify 그린"),
    "暖黃色": ("暖黄色", "Warm yellow", "暖色の黄色", "따뜻한 노란색"),
    "粉紅色": ("粉红色", "Pink", "ピンク", "분홍색"),
    "天空藍": ("天空蓝", "Sky blue", "スカイブルー", "하늘색"),
    "自訂顏色…": ("自定义颜色…", "Custom color…", "カスタム色…", "사용자 지정 색…"),
    "設定…": ("设置…", "Settings…", "設定…", "설정…"),
    "結束程式": ("退出程序", "Quit", "終了", "종료"),
    "沒有偵測到 Spotify 桌面版的播放工作階段": ("未检测到 Spotify 桌面播放会话", "Spotify desktop playback not detected", "Spotify の再生セッションが見つかりません", "Spotify 재생 세션을 찾지 못했습니다"),
    "請啟動 Spotify Windows 桌面版並播放歌曲": ("请打开 Spotify Windows 桌面版并播放歌曲", "Launch Spotify for Windows and play a song", "Windows 版 Spotify で再生してください", "Windows용 Spotify에서 노래를 재생하세요"),
    "正在搜尋這首歌的歌詞…": ("正在搜索这首歌的歌词…", "Finding lyrics for this song…", "歌詞を検索中…", "이 노래의 가사를 검색하는 중…"),
    "沒有找到歌詞": ("没有找到歌词", "Lyrics not found", "歌詞が見つかりません", "가사를 찾을 수 없습니다"),
    "歌詞查詢失敗，請稍後重試": ("歌词查询失败，请稍后重试", "Lyrics lookup failed. Please try again.", "歌詞の検索に失敗しました。後でもう一度お試しください。", "가사 검색에 실패했습니다. 나중에 다시 시도하세요."),
    "沒有歌曲": ("没有歌曲", "No track", "曲がありません", "재생 중인 곡 없음"),
    "請先在 Spotify 桌面版播放一首歌曲。": ("请先在 Spotify 桌面版播放歌曲。", "Play a song in the Spotify desktop app first.", "Spotify デスクトップアプリで楽曲を再生してください。", "먼저 Spotify 데스크톱 앱에서 노래를 재생하세요."),
    "目前使用已儲存的本機 LRC 歌詞": ("当前使用已保存的本地 LRC 歌词", "Using an existing saved local LRC file", "保存済みローカル LRC を使用中", "저장된 로컬 LRC 가사를 사용 중"),
    "歌詞服務連線失敗，請檢查網路後重試": ("歌词服务连接失败，请检查网络后重试", "Lyrics service connection failed. Check your network and try again.", "歌詞サービスに接続できません。ネットワークを確認してください。", "가사 서비스 연결 실패. 네트워크를 확인하고 다시 시도하세요."),
    "歌詞讀取發生錯誤": ("歌词读取出错", "Failed to read lyrics", "歌詞を読み込めません", "가사 읽기 오류"),
    "這是一首純演奏歌曲": ("这是一首纯演奏歌曲", "Instrumental track", "インストゥルメンタル曲", "연주곡"),
    "缺少 Windows 媒體支援套件，請重新執行啟動腳本以安裝依賴": ("缺少 Windows 媒体支持组件，请重新运行启动脚本", "Windows media support is missing; rerun the launcher to install requirements.", "Windows メディアサポートが不足しています。起動スクリプトを再実行してください。", "Windows 미디어 지원 패키지가 없습니다. 실행 스크립트를 다시 실행하세요."),
    "無法存取 Windows 媒體工作階段；請確認系統為 Windows 10/11": ("无法访问 Windows 媒体会话；请确认系统为 Windows 10/11", "Cannot access Windows media sessions. Requires Windows 10/11.", "Windows メディアセッションにアクセスできません。Windows 10/11 が必要です。", "Windows 미디어 세션에 액세스할 수 없습니다. Windows 10/11이 필요합니다."),
    "Windows 媒體偵測暫時失敗，正在重試": ("Windows 媒体检测暂时失败，正在重试", "Windows media detection failed temporarily. Retrying…", "メディア検出に失敗しました。再試行中…", "Windows 미디어 감지 실패. 다시 시도 중…"),
    "重新搜尋多來源同步歌詞中…": ("正在重新搜索多来源同步歌词…", "Refreshing synchronized lyrics…", "同期歌詞を再検索中…", "동기화 가사 다시 검색 중…"),
    "無法設定開機啟動": ("无法设置开机启动", "Could not set startup preference", "自動起動を設定できません", "자동 시작을 설정할 수 없음"),
    "OBS 來源尚未啟動": ("OBS 来源尚未启动", "OBS source not running", "OBS ソースが起動していません", "OBS 소스가 실행되지 않음"),
    "請先在設定中啟用 OBS 瀏覽器來源。": ("请先在设置中启用 OBS 浏览器来源。", "Enable the OBS browser source in Settings first.", "設定で OBS ブラウザーソースを有効にしてください。", "설정에서 OBS 브라우저 소스를 먼저 활성화하세요."),
    "OBS 來源網址已複製": ("OBS 来源网址已复制", "OBS URL copied", "OBS URL をコピーしました", "OBS URL 복사됨"),
    "滑鼠穿透已啟用": ("鼠标穿透已启用", "Click-through enabled", "マウス透過を有効化しました", "클릭 통과 활성화됨"),
    "右鍵點選右下角系統匣圖示可取消滑鼠穿透。": ("右键单击系统托盘图标可关闭鼠标穿透。", "Right-click the tray icon to disable click-through.", "通知領域のアイコンを右クリックして解除します。", "트레이 아이콘을 우클릭하여 클릭 통과를 해제하세요."),
    "上一首": ("上一首", "Previous", "前の曲", "이전 곡"),
    "下一首": ("下一首", "Next", "次の曲", "다음 곡"),
    "播放": ("播放", "Play", "再生", "재생"),
    "暫停": ("暂停", "Pause", "一時停止", "일시 정지"),
    "控制失敗": ("控制失败", "Playback control failed", "再生操作に失敗しました", "재생 제어 실패"),
    "Spotify 不支援此媒體控制指令。": ("Spotify 不支持此媒体控制指令。", "Spotify declined this media command.", "Spotify がこの操作を受け付けませんでした。", "Spotify가 이 제어 명령을 거부했습니다."),
    "找不到正在執行的 Spotify 播放工作階段。": ("找不到正在运行的 Spotify 播放会话。", "No active Spotify playback session was found.", "Spotify の再生セッションが見つかりません。", "Spotify 재생 세션을 찾지 못했습니다."),
}


def tr(text: str, **kwargs: object) -> str:
    if _active == "zh_TW":
        output = text
    else:
        slot = {"zh_CN": 0, "en": 1, "ja": 2, "ko": 3}.get(_active, 1)
        output = _TEXT.get(text, (text, text, text, text))[slot]
    return output.format(**kwargs) if kwargs else output
