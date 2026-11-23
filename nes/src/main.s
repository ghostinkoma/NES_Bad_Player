; =============================================================================
; main.s - 第1フェーズ: エミュレータ連携お絵描き表示 ROM
;
; ・ファミコンは「表示ユニット」としてだけ動く。
;   外部（Lua ブリッジ。将来は RP2354）が CPU RAM のメールボックスに
;   「PPU のどこに何を書くか」を置き、6502 は VBlank 中にそれを PPU へ流し込むだけ。
; ・パッド状態は毎フレーム MB_PAD に書き出し、外部が読み取る。
;
; 画面構成
;   キャンバス 128x120 ドット = 16x15 タイル（タイル番号 0〜239 を順番に配置）
;   画面上のタイル位置 (8,8) 〜 (23,22)
;   キャンバス外はタイル 254（細かいドット模様）
;   パターンは CHR-RAM（8KB）。外部から書き換える。
;
; マッパー: UNROM (iNES mapper 2), PRG 32KB, CHR-RAM 8KB
;   ※バンク切り替えは使わない。全コードは固定バンク $C000-$FFFF に置く。
; =============================================================================

.include "nes.inc"
.include "mailbox.inc"

CANVAS_COL   = 8      ; キャンバス左上のタイル座標
CANVAS_ROW   = 8
CANVAS_W     = 16     ; キャンバス幅（タイル）
CANVAS_H     = 15     ; キャンバス高さ（タイル）
TILE_OUTSIDE = 254    ; キャンバス外に敷くタイル

CANVAS_NT_ADDR = $2000 + CANVAS_ROW * 32 + CANVAS_COL

; -----------------------------------------------------------------------------
; iNES ヘッダ（16バイト）: エミュレータ/マジコンが ROM の構成を知るための情報
; -----------------------------------------------------------------------------
.segment "HEADER"
    .byte "NES", $1A
    .byte 2             ; PRG-ROM: 16KB x 2 = 32KB
    .byte 0             ; CHR-ROM: 0 → CHR-RAM 8KB を使う
    .byte $21           ; 上位4bit=マッパー番号下位(2), bit0=1:垂直ミラーリング
    .byte $00           ; マッパー番号上位 = 0
    .byte 0, 0, 0, 0, 0, 0, 0, 0

; -----------------------------------------------------------------------------
; ゼロページ変数
; -----------------------------------------------------------------------------
.segment "ZEROPAGE"
nmi_count: .res 1       ; NMI ごとに +1
pad:       .res 1       ; パッド1の状態
ptr:       .res 2       ; 汎用ポインタ
tmp:       .res 1

; -----------------------------------------------------------------------------
; 未使用の切り替えバンク（ファイルサイズを 32KB にするため $FF で埋める）
; -----------------------------------------------------------------------------
.segment "BANK0"
    .byte $FF

; =============================================================================
.segment "CODE"
; =============================================================================

; -----------------------------------------------------------------------------
; リセット: 電源ON / リセットボタンで最初に実行される
; -----------------------------------------------------------------------------
.proc reset
    sei                 ; IRQ 禁止
    cld                 ; 10進モード解除（ファミコンの CPU には無いが念のため）
    ldx #$40
    stx JOY2_FRAME      ; APU フレーム IRQ 禁止
    ldx #$FF
    txs                 ; スタックポインタ初期化
    inx                 ; X = 0
    stx PPUCTRL         ; NMI 禁止
    stx PPUMASK         ; 描画 OFF
    stx APU_DMC         ; DMC IRQ 禁止

    ; PPU の起動待ち 1回目
    bit PPUSTATUS
@vblank1:
    bit PPUSTATUS
    bpl @vblank1

    ; RAM ($0000-$07FF) を 0 クリア
    lda #0
@clear_ram:
    sta $0000, x
    sta $0100, x
    sta $0200, x
    sta $0300, x
    sta $0400, x
    sta $0500, x
    sta $0600, x
    sta $0700, x
    inx
    bne @clear_ram

    ; PPU の起動待ち 2回目（これ以降 PPU に書き込める）
@vblank2:
    bit PPUSTATUS
    bpl @vblank2

    jsr init_chr
    jsr init_nametable
    jsr init_palette

    ; 外部へ「準備完了」を通知
    lda #MB_READY_MAGIC
    sta MB_READY

    ; VBlank を待ってから描画 ON（初回のちらつき防止）
@vblank3:
    bit PPUSTATUS
    bpl @vblank3
    lda #0
    sta PPUSCROLL
    sta PPUSCROLL
    lda #CTRL_NMI                   ; NMI 許可, BG パターン = $0000
    sta PPUCTRL
    lda #(MASK_BG | MASK_BG_LEFT)   ; BG のみ表示
    sta PPUMASK

; -----------------------------------------------------------------------------
; メインループ: 1フレームに1回パッドを読み、メールボックスへ書き出す
; -----------------------------------------------------------------------------
main_loop:
    lda nmi_count
@wait_nmi:
    cmp nmi_count       ; NMI で nmi_count が変わるまで待つ
    beq @wait_nmi

    jsr readpad
    lda pad
    sta MB_PAD
    lda nmi_count
    sta MB_FRAME
    jmp main_loop
.endproc

; -----------------------------------------------------------------------------
; CHR-RAM 初期化: 8KB を 0 クリアし、タイル 254 に模様を書く
; -----------------------------------------------------------------------------
.proc init_chr
    bit PPUSTATUS
    lda #$00
    sta PPUADDR
    sta PPUADDR
    ldy #$20            ; 32 x 256 = 8192 バイト
    ldx #0
@loop:
    sta PPUDATA
    inx
    bne @loop
    dey
    bne @loop

    ; タイル 254 = $0FE0
    lda #$0F
    sta PPUADDR
    lda #$E0
    sta PPUADDR
    ldx #0
@tile:
    lda tile_outside, x
    sta PPUDATA
    inx
    cpx #16
    bne @tile
    rts
.endproc

; -----------------------------------------------------------------------------
; ネームテーブル初期化
;   全面をタイル 254 で埋め、属性を 0 にし、キャンバス部分にタイル 0〜239 を並べる
; -----------------------------------------------------------------------------
.proc init_nametable
    bit PPUSTATUS
    lda #$20
    sta PPUADDR
    lda #$00
    sta PPUADDR

    lda #TILE_OUTSIDE
    ldy #3              ; 768 バイト
    ldx #0
@fill1:
    sta PPUDATA
    inx
    bne @fill1
    dey
    bne @fill1
    ldx #192            ; +192 = 960 バイト
@fill2:
    sta PPUDATA
    dex
    bne @fill2

    lda #0              ; 属性テーブル 64 バイト
    ldx #64
@attr:
    sta PPUDATA
    dex
    bne @attr

    ; キャンバス: 1行ごとに PPU アドレスを設定して 16 タイル書く
    lda #<CANVAS_NT_ADDR
    sta ptr
    lda #>CANVAS_NT_ADDR
    sta ptr+1
    lda #0
    sta tmp             ; 次に置くタイル番号
    ldy #CANVAS_H
@row:
    lda ptr+1
    sta PPUADDR
    lda ptr
    sta PPUADDR
    ldx #CANVAS_W
@col:
    lda tmp
    sta PPUDATA
    inc tmp
    dex
    bne @col

    lda ptr             ; ptr += 32（次の行）
    clc
    adc #32
    sta ptr
    bcc :+
    inc ptr+1
:
    dey
    bne @row
    rts
.endproc

; -----------------------------------------------------------------------------
; パレット初期化（32バイト: BG 16 + スプライト 16）
; -----------------------------------------------------------------------------
.proc init_palette
    bit PPUSTATUS
    lda #$3F
    sta PPUADDR
    lda #$00
    sta PPUADDR
    ldx #0
@loop:
    lda default_palette, x
    sta PPUDATA
    inx
    cpx #32
    bne @loop
    rts
.endproc

; -----------------------------------------------------------------------------
; パッド1読み取り → pad（bit7から A B SELECT START 上 下 左 右）
; -----------------------------------------------------------------------------
.proc readpad
    lda #1
    sta JOY1            ; ストローブ ON
    sta pad             ; bit0 に 1 を入れておき、8回シフトで carry に出たら終了
    lsr a               ; A = 0
    sta JOY1            ; ストローブ OFF
@loop:
    lda JOY1
    lsr a               ; bit0 → carry
    rol pad             ; carry → pad
    bcc @loop
    rts
.endproc

; -----------------------------------------------------------------------------
; NMI: VBlank 開始時に毎フレーム呼ばれる
;   メールボックスに転送要求があれば PPU へコピーする（最大 128 バイト）
;   128 バイトでも約 1300 サイクルで、VBlank（約 2270 サイクル）に収まる
; -----------------------------------------------------------------------------
.proc nmi
    pha
    txa
    pha
    tya
    pha

    lda MB_FLAG
    beq @done

    ldy MB_COUNT8
    beq @ack                    ; 0 は不正 → 捨てる
    cpy #MB_MAX_COUNT8 + 1
    bcs @ack                    ; 17 以上も不正 → 捨てる

    bit PPUSTATUS               ; $2006 の書き込み順をリセット
    lda MB_ADDR_HI
    sta PPUADDR
    lda MB_ADDR_LO
    sta PPUADDR

    ldx #0
@copy:                          ; 8 バイトずつ展開してコピー（高速化）
    .repeat 8, I
        lda MB_BUF + I, x
        sta PPUDATA
    .endrepeat
    txa
    clc
    adc #8
    tax
    dey
    bne @copy

@ack:
    lda #0
    sta MB_FLAG                 ; 処理完了を外部へ通知

@done:
    ; $2006 を触るとスクロール位置が壊れるので毎回リセット
    bit PPUSTATUS
    lda #0
    sta PPUSCROLL
    sta PPUSCROLL
    lda #CTRL_NMI
    sta PPUCTRL

    inc nmi_count

    pla
    tay
    pla
    tax
    pla
    rti
.endproc

.proc irq
    rti
.endproc

; =============================================================================
.segment "RODATA"
; =============================================================================

; 初期パレット（tools/paint/nes_paint.py の DEFAULT_PALETTE と同じ値）
;   $3F00 共通背景色, 以降 [x, c1, c2, c3] x 4
default_palette:
    .byte $30, $0F, $00, $10    ; パレット0: 白背景 / 黒・灰・明灰
    .byte $30, $06, $16, $27    ; パレット1: 赤系
    .byte $30, $09, $19, $2A    ; パレット2: 緑系
    .byte $30, $01, $11, $21    ; パレット3: 青系
    ; スプライト用（未使用だが $3F10 は $3F00 のミラーなので同じ値にしておく）
    .byte $30, $0F, $00, $10
    .byte $30, $06, $16, $27
    .byte $30, $09, $19, $2A
    .byte $30, $01, $11, $21

; キャンバス外のタイル: 4ドット間隔の点（色1）
tile_outside:
    .byte $88, $00, $00, $00, $88, $00, $00, $00   ; プレーン0
    .byte $00, $00, $00, $00, $00, $00, $00, $00   ; プレーン1

; =============================================================================
.segment "VECTORS"
; =============================================================================
    .word nmi       ; $FFFA
    .word reset     ; $FFFC
    .word irq       ; $FFFE
