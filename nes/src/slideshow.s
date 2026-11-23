; =============================================================================
; slideshow.s - 第3フェーズ: スライドショー ROM（マジコン / 実機確認用）
;
; ・第2フェーズで変換した画像（128x120）を ROM に最大 21 枚埋め込み、パッドで切り替える。
; ・画面の作りは第1フェーズと同じ（キャンバス = タイル 0〜239 を画面中央に並べる）。
;
; 操作
;   → / A       次の画像
;   ← / B       前の画像
;   START       自動再生 ON/OFF（AUTO_SECONDS 秒ごとに次へ）
;   SELECT      表示（番号・パッド表示）の ON/OFF
;   画面上: 「今の番号/枚数」と状態（▶ 自動再生中 / ⌛ 読み込み中）
;   画面下: パッドの状態 □=離している ■=押している（左から A B SELECT START 上 下 左 右）
;
; 画像の切り替え（ちらつき防止のダブルバッファ）
;   CHR-RAM 8KB = パターンテーブル 2 枚（$0000 / $1000）。表示していない方へ
;   VBlank ごとに CHUNK バイトずつ書き込み（30 フレーム = 約 0.5 秒）、書き終わったら
;   1 回の VBlank で属性・パレットを書き換え、BG のパターンテーブルを切り替える。
;   → 第4フェーズで RP2354 から画像を流し込むときと同じ「VBlank 内に少しずつ書く」方式で、
;     実機で VBlank の時間内に収まっているか（表示崩れがないか）の確認も兼ねる。
;
; マッパー: UNROM (iNES mapper 2), PRG 128KB, CHR-RAM 8KB
;   バンク 0〜6 ($8000-$BFFF に切り替え): 画像データ（1 バンクに 3 枚）
;     +$0000 / +$1000 / +$2000  画像 0/1/2 の CHR（240 タイル = 3840 バイト）
;     +$3000 / +$3080 / +$3100  画像 0/1/2 の属性 64 バイト + BG パレット 16 バイト
;   バンク 7 ($C000-$FFFF 固定): このプログラム
; =============================================================================

.include "nes.inc"

CANVAS_COL   = 8      ; キャンバス左上のタイル座標（第1フェーズと同じ）
CANVAS_ROW   = 8
CANVAS_W     = 16
CANVAS_H     = 15
CANVAS_NT_ADDR = $2000 + CANVAS_ROW * 32 + CANVAS_COL

; 表示用タイル（nes/src/ui_tiles.inc。tools/slideshow/gen_ui_tiles.py と一致させる）
TILE_UI_FIRST = 240
TILE_DIGIT0   = 240
TILE_SLASH    = 250
TILE_PLAY     = 251
TILE_BOX_OFF  = 252
TILE_BOX_ON   = 253
TILE_OUTSIDE  = 254
TILE_BUSY     = 255

CHR_BYTES    = 240 * 16          ; 1 枚の CHR（キャンバスのタイル 0〜239）
CHUNK        = 128               ; 1 回の VBlank で書くバイト数
CHUNKS       = CHR_BYTES / CHUNK ; 30
.assert CHR_BYTES .mod CHUNK = 0, error, "CHUNK は 3840 を割り切る値にすること"
AUTO_SECONDS = 5                 ; 自動再生の間隔（秒）

; スプライト表示の位置（スプライトの Y は「表示したい行 - 1」を書く）
UI_COUNTER_X = 104
UI_COUNTER_Y = 48 - 1            ; キャンバス（Y=64〜183）の上
UI_PAD_X     = 96
UI_PAD_Y     = 192 - 1           ; キャンバスの下

OAM = $0200

; -----------------------------------------------------------------------------
; iNES ヘッダ
; -----------------------------------------------------------------------------
.segment "HEADER"
    .byte "NES", $1A
    .byte 8             ; PRG-ROM: 16KB x 8 = 128KB
    .byte 0             ; CHR-ROM なし → CHR-RAM 8KB
    .byte $21           ; マッパー 2 (UNROM), 垂直ミラーリング
    .byte $00
    .byte 0, 0, 0, 0, 0, 0, 0, 0

; -----------------------------------------------------------------------------
; 画像データ（make_slides.py が build/slides/bank0.bin〜bank6.bin を作る）
; -----------------------------------------------------------------------------
.segment "BANK0"
    .incbin "bank0.bin"
.segment "BANK1"
    .incbin "bank1.bin"
.segment "BANK2"
    .incbin "bank2.bin"
.segment "BANK3"
    .incbin "bank3.bin"
.segment "BANK4"
    .incbin "bank4.bin"
.segment "BANK5"
    .incbin "bank5.bin"
.segment "BANK6"
    .incbin "bank6.bin"

; -----------------------------------------------------------------------------
; 変数
; -----------------------------------------------------------------------------
.segment "ZEROPAGE"
nmi_count:   .res 1
pad:         .res 1     ; 今のパッド
pad_prev:    .res 1     ; 前のフレームのパッド
pad_new:     .res 1     ; このフレームで押されたボタン
tmp:         .res 1
tmp2:        .res 1
oam_x:       .res 1
oam_y:       .res 1

; 画像の転送（NMI と main の受け渡し。phase を最後に書いて開始の合図にする）
phase:       .res 1     ; 0=何もしない 1=CHR 転送中 2=最後の仕上げ（属性・パレット・切り替え）
src:         .res 2     ; CHR の読み出し元（ROM, $8000-$BFFF）
meta:        .res 2     ; 属性+パレットの読み出し元
dst_hi:      .res 1     ; CHR の書き込み先（PPU アドレス）
dst_lo:      .res 1
chunks:      .res 1     ; 残りの転送回数
front:       .res 1     ; 表示中の BG パターンテーブル 0=$0000 1=$1000
loading_idx: .res 1     ; 転送中の画像番号

shown:       .res 1     ; 表示中の画像番号
want:        .res 1     ; 表示したい画像番号（パッドで変わる）
auto:        .res 1     ; 自動再生 0/1
auto_frames: .res 1
auto_secs:   .res 1
ui_on:       .res 1     ; 表示 ON/OFF

.segment "OAM"
oam_buf:     .res 256
.assert oam_buf = OAM, error, "OAM は $0200 に置くこと"

; =============================================================================
.segment "CODE"
; =============================================================================

; -----------------------------------------------------------------------------
; リセット
; -----------------------------------------------------------------------------
.proc reset
    sei
    cld
    ldx #$40
    stx JOY2_FRAME
    ldx #$FF
    txs
    inx
    stx PPUCTRL
    stx PPUMASK
    stx APU_DMC

    bit PPUSTATUS
@vblank1:
    bit PPUSTATUS
    bpl @vblank1

    lda #0
@clear_ram:
    sta $0000, x
    sta $0100, x
    sta $0300, x
    sta $0400, x
    sta $0500, x
    sta $0600, x
    sta $0700, x
    inx
    bne @clear_ram
    jsr hide_all_sprites            ; $0200 は $FF（画面外）で埋める

@vblank2:
    bit PPUSTATUS
    bpl @vblank2

    jsr init_ui_tiles
    jsr init_nametable
    jsr init_palette

    ; 1 枚目を描画 OFF のうちにまとめて書く（表と裏を入れ替えて $0000 側へ）
    lda #1
    sta front
    lda #0
    sta want
    jsr start_load
@first:
    jsr upload_step
    lda phase
    bne @first

    lda #1
    sta ui_on
    jsr build_oam

@vblank3:
    bit PPUSTATUS
    bpl @vblank3
    lda #0
    sta OAMADDR
    lda #>OAM
    sta OAMDMA
    lda #0
    sta PPUSCROLL
    sta PPUSCROLL
    jsr ctrl_value
    sta PPUCTRL
    lda #(MASK_BG | MASK_BG_LEFT | MASK_SPR | MASK_SPR_LEFT)
    sta PPUMASK

; -----------------------------------------------------------------------------
; メインループ（1 フレームに 1 回）
; -----------------------------------------------------------------------------
main_loop:
    lda nmi_count
@wait_nmi:
    cmp nmi_count
    beq @wait_nmi

    jsr readpad
    lda pad_prev
    eor #$FF
    and pad
    sta pad_new
    lda pad
    sta pad_prev

    jsr handle_input
    jsr auto_tick

    ; 転送していなくて、表示したい画像が今と違えば転送を始める
    lda phase
    bne @busy
    lda want
    cmp shown
    beq @busy
    jsr start_load
@busy:
    jsr build_oam
    jmp main_loop
.endproc

; -----------------------------------------------------------------------------
; パッド操作
; -----------------------------------------------------------------------------
.proc handle_input
    lda pad_new
    and #(PAD_RIGHT | PAD_A)
    beq :+
    jsr next_slide
    jsr reset_auto
:   lda pad_new
    and #(PAD_LEFT | PAD_B)
    beq :+
    jsr prev_slide
    jsr reset_auto
:   lda pad_new
    and #PAD_START
    beq :+
    lda auto
    eor #1
    sta auto
    jsr reset_auto
:   lda pad_new
    and #PAD_SELECT
    beq :+
    lda ui_on
    eor #1
    sta ui_on
:   rts
.endproc

.proc next_slide
    ldx want
    inx
    cpx #SLIDE_COUNT
    bcc :+
    ldx #0
:   stx want
    rts
.endproc

.proc prev_slide
    ldx want
    bne :+
    ldx #SLIDE_COUNT
:   dex
    stx want
    rts
.endproc

.proc reset_auto
    lda #0
    sta auto_frames
    sta auto_secs
    rts
.endproc

; 自動再生: 表示が終わってから AUTO_SECONDS 秒たったら次へ（読み込み中は数えない）
.proc auto_tick
    lda auto
    beq @done
    lda phase
    bne @done
    inc auto_frames
    lda auto_frames
    cmp #60
    bcc @done
    lda #0
    sta auto_frames
    inc auto_secs
    lda auto_secs
    cmp #AUTO_SECONDS
    bcc @done
    lda #0
    sta auto_secs
    jsr next_slide
@done:
    rts
.endproc

; -----------------------------------------------------------------------------
; 画像 want の転送を始める（phase = 0 のときだけ呼ぶこと）
; -----------------------------------------------------------------------------
.proc start_load
    ldx want
    stx loading_idx

    ; バンク切り替え。UNROM は書き込み先の ROM の値と書く値がぶつかる（バスコンフリクト）
    ; 基板があるので、同じ値が入っている banktable の場所に書く
    ldy slide_bank, x
    tya
    sta banktable, y

    lda slide_chr_hi, x
    sta src+1
    lda #0
    sta src
    lda slide_meta_lo, x
    sta meta
    lda slide_meta_hi, x
    sta meta+1

    lda front                       ; 表示していない方のパターンテーブルへ書く
    beq :+
    lda #$00
    beq :++
:   lda #$10
:   sta dst_hi
    lda #0
    sta dst_lo

    lda #CHUNKS
    sta chunks
    lda #1
    sta phase                       ; 最後に書く = NMI への開始の合図
    rts
.endproc

; -----------------------------------------------------------------------------
; 転送を 1 回分進める（NMI から呼ぶ。描画 OFF の間は何回呼んでもよい）
;   phase 1: CHR を CHUNK バイト。phase 2: 属性 64 + パレット 16 バイトと表裏の切り替え
;   src / meta は 128 バイト境界にあるので (src),y がページをまたがず 1 バイト 11 サイクル
; -----------------------------------------------------------------------------
.proc upload_step
    lda phase
    bne :+
    rts
:   cmp #1
    beq @chr
    jmp @finish                     ; 展開したループの向こうなので分岐命令では届かない

@chr:
    bit PPUSTATUS
    lda dst_hi
    sta PPUADDR
    lda dst_lo
    sta PPUADDR
    ldy #0
    .repeat ::CHUNK                 ; .proc の中では「::」でグローバルの定数を指定する
        lda (src), y
        sta PPUDATA
        iny
    .endrepeat

    lda src                         ; src += CHUNK
    clc
    adc #CHUNK
    sta src
    bcc :+
    inc src+1
:   lda dst_lo                      ; dst += CHUNK
    clc
    adc #CHUNK
    sta dst_lo
    bcc :+
    inc dst_hi
:   dec chunks
    bne @done
    lda #2
    sta phase
@done:
    rts

@finish:
    bit PPUSTATUS
    lda #$23                        ; 属性テーブル $23C0
    sta PPUADDR
    lda #$C0
    sta PPUADDR
    ldy #0
    .repeat 64
        lda (meta), y
        sta PPUDATA
        iny
    .endrepeat
    lda #$3F                        ; BG パレット $3F00（スプライト側は触らない）
    sta PPUADDR
    lda #$00
    sta PPUADDR
    .repeat 16
        lda (meta), y
        sta PPUDATA
        iny
    .endrepeat

    lda front                       ; 表裏を入れ替える（PPUCTRL は NMI の最後に書く）
    eor #1
    sta front
    lda loading_idx
    sta shown
    lda #0
    sta phase
    rts
.endproc

; PPUCTRL に書く値: NMI 許可 + BG パターンテーブル（スプライトは常に $0000）
.proc ctrl_value
    lda front
    beq :+
    lda #CTRL_BG_1000
:   ora #CTRL_NMI
    rts
.endproc

; -----------------------------------------------------------------------------
; 表示用タイル 240〜255 を両方のパターンテーブルに書く
; -----------------------------------------------------------------------------
.proc init_ui_tiles
    lda #$0F
    jsr @copy
    lda #$1F
@copy:
    bit PPUSTATUS
    sta PPUADDR
    lda #$00
    sta PPUADDR
    ldx #0
:   lda ui_tiles, x
    sta PPUDATA
    inx
    bne :-                          ; 16 タイル x 16 = 256 バイト
    rts
.endproc

; -----------------------------------------------------------------------------
; ネームテーブル: 全面タイル 254、キャンバス部分にタイル 0〜239（第1フェーズと同じ）
; -----------------------------------------------------------------------------
.proc init_nametable
    bit PPUSTATUS
    lda #$20
    sta PPUADDR
    lda #$00
    sta PPUADDR

    lda #TILE_OUTSIDE
    ldy #3
    ldx #0
@fill1:
    sta PPUDATA
    inx
    bne @fill1
    dey
    bne @fill1
    ldx #192
@fill2:
    sta PPUDATA
    dex
    bne @fill2

    lda #0
    ldx #64
@attr:
    sta PPUDATA
    dex
    bne @attr

    lda #<CANVAS_NT_ADDR
    sta src
    lda #>CANVAS_NT_ADDR
    sta src+1
    lda #0
    sta tmp
    ldy #CANVAS_H
@row:
    lda src+1
    sta PPUADDR
    lda src
    sta PPUADDR
    ldx #CANVAS_W
@col:
    lda tmp
    sta PPUDATA
    inc tmp
    dex
    bne @col
    lda src
    clc
    adc #32
    sta src
    bcc :+
    inc src+1
:   dey
    bne @row
    rts
.endproc

; パレット 32 バイト（BG は 1 枚目の読み込みで上書きされる。スプライト側は表示用の色）
.proc init_palette
    bit PPUSTATUS
    lda #$3F
    sta PPUADDR
    lda #$00
    sta PPUADDR
    ldx #0
:   lda init_palette_data, x
    sta PPUDATA
    inx
    cpx #32
    bne :-
    rts
.endproc

; -----------------------------------------------------------------------------
; スプライト表（OAM）を作る。NMI が毎フレーム DMA で PPU へ送る
; -----------------------------------------------------------------------------
.proc build_oam
    lda ui_on
    bne :+
    jmp hide_all_sprites
:   ldx #0

    ; 「番号/枚数」
    lda #UI_COUNTER_Y
    sta oam_y
    lda #UI_COUNTER_X
    sta oam_x
    lda shown
    clc
    adc #1
    jsr put_number
    lda #TILE_SLASH
    jsr put_spr
    lda #SLIDE_COUNT
    jsr put_number

    ; 状態: 読み込み中 ⌛ / 自動再生中 ▶
    lda phase
    beq :+
    lda #TILE_BUSY
    jsr put_spr
    jmp @pad
:   lda auto
    beq @pad
    lda #TILE_PLAY
    jsr put_spr

@pad:
    ; パッド表示（bit7 から A B SELECT START 上 下 左 右）
    lda #UI_PAD_Y
    sta oam_y
    lda #UI_PAD_X
    sta oam_x
    lda #$80
    sta tmp2
@bit:
    lda pad
    and tmp2
    beq @off
    lda #TILE_BOX_ON
    bne @put
@off:
    lda #TILE_BOX_OFF
@put:
    jsr put_spr
    lsr tmp2
    bne @bit

    lda #$FF                        ; 残りは画面外へ
@rest:
    sta OAM, x
    inx
    bne @rest
    rts
.endproc

.proc hide_all_sprites
    lda #$FF
    ldx #0
:   sta OAM, x
    inx
    bne :-
    rts
.endproc

; A = 0〜99 を 2 桁で置く
.proc put_number
    ldy #0
@tens:
    cmp #10
    bcc @ones
    sbc #10                         ; carry = 1 のまま
    iny
    bne @tens
@ones:
    sta tmp
    tya
    clc
    adc #TILE_DIGIT0
    jsr put_spr
    lda tmp
    clc
    adc #TILE_DIGIT0
    ; そのまま put_spr へ
.endproc
; A = タイル番号を (oam_x, oam_y) に置き、oam_x += 8, X += 4
.proc put_spr
    sta OAM+1, x
    lda oam_y
    sta OAM, x
    lda #0                          ; スプライトパレット 0, BG より手前
    sta OAM+2, x
    lda oam_x
    sta OAM+3, x
    clc
    adc #8
    sta oam_x
    inx
    inx
    inx
    inx
    rts
.endproc

; -----------------------------------------------------------------------------
; パッド1 → pad
; -----------------------------------------------------------------------------
.proc readpad
    lda #1
    sta JOY1
    sta pad
    lsr a
    sta JOY1
@loop:
    lda JOY1
    lsr a
    rol pad
    bcc @loop
    rts
.endproc

; -----------------------------------------------------------------------------
; NMI（VBlank の始まり）
;   OAM DMA（約 513 サイクル）→ 画像転送 1 回分（最大約 1450 サイクル）→ スクロール・PPUCTRL
;   VBlank は約 2273 サイクル。合計がこれに収まるよう CHUNK を決めている
; -----------------------------------------------------------------------------
.proc nmi
    pha
    txa
    pha
    tya
    pha

    lda #0
    sta OAMADDR
    lda #>OAM
    sta OAMDMA

    jsr upload_step

    bit PPUSTATUS
    lda #0
    sta PPUSCROLL
    sta PPUSCROLL
    jsr ctrl_value
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

; バスコンフリクト対策のバンク番号表（banktable + n に値 n が入っている）
banktable:
    .byte 0, 1, 2, 3, 4, 5, 6, 7

init_palette_data:
    .byte $0F, $0F, $00, $10,  $0F, $0F, $00, $10,  $0F, $0F, $00, $10,  $0F, $0F, $00, $10
    ; スプライト: 色1=白 色2=オレンジ 色3=黒（$3F10 は $3F00 のミラーなので同じ $0F）
    .byte $0F, $30, $27, $0F,  $0F, $30, $27, $0F,  $0F, $30, $27, $0F,  $0F, $30, $27, $0F

.include "ui_tiles.inc"

; スライド表（SLIDE_COUNT, slide_bank, slide_chr_hi, slide_meta_lo/hi）
.include "slides_table.inc"

; =============================================================================
.segment "VECTORS"
; =============================================================================
    .word nmi
    .word reset
    .word irq
