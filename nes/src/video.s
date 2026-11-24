; =============================================================================
; video.s - フルサイズ動画プレイヤー（フェーズ2.x: メニュー付き）
;
; マッパー BNROM（32KB 単位で切り替え）。このコードは $F000-$FFFF に置かれ、
; tools/video/make_video_rom.py が全バンクの同じ場所にコピーする。
;
; バンク 0 の $8000- は「目録」（make_video_rom.py が作る）
;   $8010 + 番号*16: 動画の情報（番号 0-3 = タイトル、4 = メニューの背景）
;     +0 1=入っている  +1 最初のバンク  +2 スナップショットの数  +3,+4 スナップショット表の場所
;   スナップショット表 1 件 8 バイト:
;     +0 バンク, +1,+2 場所 … PPU の中身（ネームテーブル 960 + 絵 256x8 + 白黒反転 1）
;     +3 バンク, +4,+5 場所 … その時点から続くパケット
;     +6,+7 その時点までに終わったコマ数
;
; 動画データ（パケット）= 絵の枚数 k, { タイル番号, 絵 8 バイト } × k, { ラン } 終端
;   ラン = PPUアドレス上位, 下位, バイト数 n (1-63), データ n バイト
;   終端 = $80 | フラグ   bit0-1: 0=次へ / 1=次のバンクの $8000 へ / 2=動画の終わり
;                          bit3  : このパケットで 1 コマ分が終わる
;
; 1 フレーム（1/60 秒）に 1 パケット:
;   メインループ … 次のパケットをスタックページ ($0100-) に「そのまま流せる形」で並べる
;   NMI（VBlank）… スタックポインタをそこに向け、pla / sta $2007 で流し込む
;
; 操作
;   メニュー: ↑↓ 選ぶ / A 再生 / SELECT 再生モード（REPEAT → STOP → SEQ）
;   再生中  : B 一時停止 / 一時停止中に → コマ送り・← コマ戻し / SELECT FPS 表示 / START メニューへ
; =============================================================================

.include "nes.inc"

BUF        = $0100      ; NMI 用バッファ（$0100-$01DF）。スタックは $01E0-$01FF だけ使う
OAM        = $0200
MAX_RUN    = 63
CAT_TITLES = $8010      ; 目録（バンク 0）: 1 件 48 バイト
ENT_SIZE   = 33         ; そのうち読む分
FONT_ADDR  = $8100      ; スプライト文字（バンク 0）
; 目録 1 件の中身（ent+N）
E_MODE     = 6          ; 0 = 白黒 / 1 = 4 段階（絵 16 バイト）
E_NSLOTS   = 7          ; 1 バンクの音声スロット数
E_SLOTREG  = 8          ; スロット 0 の $4012 の値
E_LVLHI    = 9          ; スロット 0 の開始レベルの場所（上位）
E_DMCCTRL  = 10         ; $4010 の値（ループ + rate）
E_SEG2     = 11         ; 1 セグメントの長さ（3 バイト）
E_CHUNK2   = 14         ; 64 バイトの長さ（3 バイト）
E_PAL      = 17         ; 最初の BG パレット 16 バイト
OP_SKIP    = $C0
OP_PAL2    = $C1
NTITLES    = 4
MENU_ID    = 4          ; メニューの背景（PuniNeko を右寄せにしたもの）

MODE_REPEAT = 0
MODE_STOP   = 1
MODE_SEQ    = 2

; ---- 音声（DPCM）。tools/video/make_video_rom.py の同じ名前の定数と合わせる
;   セグメント j（497 バイト）はバンクの $D800 + 512 * (j mod 12) にある。DMC はループ再生にしておき、
;   次のセグメントの場所を $4012/$4013 に先に書いておくと、終わったところで自動的に続きを鳴らす。
APU_STATUS  = $4015
DMC_FREQ    = $4010
DMC_RAW     = $4011
DMC_START   = $4012
DMC_LEN     = $4013
AUDIO_L     = 31            ; 長さ 16*31+1 = 497 バイト
LEVELS_LO   = $F1          ; スロット内の 497 バイト目から 64 バイトごとの開始レベル
FRAME2      = 59561         ; 1 フレーム（CPU サイクルの 2 倍）
MARGIN2     = 238244        ; セグメントが変わってから次の場所を書くまでの余裕（4 フレーム）

CTRL_VALUE = CTRL_NMI | %00001000   ; NMI あり / BG $0000 / スプライト $1000
MASK_PLAY  = MASK_BG | MASK_BG_LEFT
MASK_ALL   = MASK_BG | MASK_BG_LEFT | MASK_SPR | MASK_SPR_LEFT

TILE_BLANK = 40
FPS_SPR    = 3          ; FPS の数字は 3 番目と 4 番目のスプライト

; ---- 文字 → スプライトのタイル番号（tools/video/gen_font.py と合わせる）
; .repeat の中の .charmap は 1 回目しか効かないので 1 行ずつ書く
.charmap '0', 0
.charmap '1', 1
.charmap '2', 2
.charmap '3', 3
.charmap '4', 4
.charmap '5', 5
.charmap '6', 6
.charmap '7', 7
.charmap '8', 8
.charmap '9', 9
.charmap 'A', 10
.charmap 'B', 11
.charmap 'C', 12
.charmap 'D', 13
.charmap 'E', 14
.charmap 'F', 15
.charmap 'G', 16
.charmap 'H', 17
.charmap 'I', 18
.charmap 'J', 19
.charmap 'K', 20
.charmap 'L', 21
.charmap 'M', 22
.charmap 'N', 23
.charmap 'O', 24
.charmap 'P', 25
.charmap 'Q', 26
.charmap 'R', 27
.charmap 'S', 28
.charmap 'T', 29
.charmap 'U', 30
.charmap 'V', 31
.charmap 'W', 32
.charmap 'X', 33
.charmap 'Y', 34
.charmap 'Z', 35
.charmap '-', 36
.charmap ':', 37
.charmap '>', 38
.charmap '/', 39
.charmap ' ', $FF       ; 空白はスプライトを置かずに進む
STR_END = $FE           ; 文字列の終わり（0 は数字の 0 なので使えない）

.segment "ZEROPAGE"
nmi_count:  .res 1
ready:      .res 1      ; 1 = バッファの準備ができた（NMI が流したら 0 に戻す）
ppu_busy:   .res 1      ; 1 = メインループが PPU を直接使っている（NMI は PPU に触らない）
mask:       .res 1      ; NMI が毎回 PPUMASK に書く値
oam_dirty:  .res 1      ; 1 = 次の NMI はパケットの代わりにスプライトを書き直す（メニューのみ）
patch_n:    .res 1      ; 1 = 次の NMI で FPS の数字 2 つを書き換える
patch_d1:   .res 1
patch_d2:   .res 1
vp:         .res 2      ; 次に読むパケットの位置
bank:       .res 1
last_term:  .res 1
saved_s:    .res 1
tmp:        .res 1
tmp2:       .res 1
ptr:        .res 2
pad:        .res 1
pad_prev:   .res 1
pad_new:    .res 1
state:      .res 1      ; 0 = メニュー / 1 = 再生中 / 2 = 電源を入れた直後のオープニング（終わるとメニューへ）
stream:     .res 1      ; いま流している動画の番号（0-3 / 4 = メニュー）
sel:        .res 1      ; メニューのカーソル
mode:       .res 1      ; MODE_*
fps_on:     .res 1
paused:     .res 1
stepping:   .res 1
at_end:     .res 1      ; 1 = 最後のパケットまで来た
frames:     .res 2      ; 今の動画で終わったコマ数
target:     .res 2
sec_tick:   .res 1
fps_count:  .res 1
enabled:    .res NTITLES
ent:        .res ENT_SIZE ; 目録から読んだ 1 件（+5 = 音声あり、ほかは E_*）
naddr:      .res 2      ; build_packet: ネームテーブルの今の位置
eaddr:      .res 2
tmp3:       .res 1
tmp4:       .res 1
snap:       .res 16     ; 選んだスナップショット
audio_ok:   .res 1      ; 今の動画に音声がある
audio_on:   .res 1      ; DMC が鳴っている
dmc_pending:.res 1      ; 1 = 次の NMI で DMC を鳴らし始める
need_write: .res 1      ; 1 = セグメントが変わったので、余裕を見て次の場所を書く
nx4012:     .res 1      ; 鳴らし始めたあとに書く「次のセグメント」の場所
acc:        .res 3      ; 音声時計: 今のセグメントの頭からの時間（CPU サイクルの 2 倍）
aslot:      .res 1      ; 音声時計: 今のセグメントのスロット（0-11）
built:      .res 1      ; 1 = このループでパケットを作った
tclk:       .res 3
oam_i:      .res 1
tx:         .res 1
ty:         .res 1
tattr:      .res 1
cnt:        .res 2

.segment "CODE"

; -----------------------------------------------------------------------------
reset:
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
    stx APU_STATUS
    bit PPUSTATUS
@vb1:
    bit PPUSTATUS
    bpl @vb1
    txa
@clr:
    sta $0000,x
    sta $0100,x
    sta $0300,x
    sta $0400,x
    sta $0500,x
    sta $0600,x
    sta $0700,x
    inx
    bne @clr
@vb2:
    bit PPUSTATUS
    bpl @vb2

    ; どの動画が入っているか
    ldx #0
@en:
    stx tmp2
    jsr read_entry
    ldx tmp2
    lda ent
    sta enabled,x
    inx
    cpx #NTITLES
    bne @en

    ; スプライト文字を $1000 へ
    bit PPUSTATUS
    lda #$10
    sta PPUADDR
    lda #$00
    sta PPUADDR
    lda #<FONT_ADDR         ; バンク 0（read_entry で切り替え済み）
    sta ptr
    lda #>FONT_ADDR
    sta ptr+1
    ldx #3                  ; 41 タイル = 656 バイト → 3 ページ分写す（余りは無害）
    ldy #0
@font:
    lda (ptr),y
    sta PPUDATA
    iny
    bne @font
    inc ptr+1
    dex
    bne @font

    lda #0
    sta mode
    sta fps_on
    lda #1
    sta sel
    ldx #0                  ; まずオープニング（入っていなければメニュー）
    lda enabled
    beq @menu
    lda #2
    sta state
    jsr stream_start
    jmp @go
@menu:
    lda #0
    sta state
    ldx #MENU_ID
    jsr stream_start
@go:
    lda #CTRL_VALUE
    sta PPUCTRL

; -----------------------------------------------------------------------------
main_loop:
    lda nmi_count
@wait:
    cmp nmi_count
    beq @wait
    jsr readpad
    lda state
    bne @play
    jsr menu_frame
    jmp main_loop
@play:
    jsr play_frame
    jmp main_loop

; ---- メニュー ---------------------------------------------------------------
menu_frame:
    lda pad_new
    and #PAD_UP
    beq @no_up
    ldx sel
:   dex                     ; 入っていない動画は飛ばす
    txa
    and #3
    tax
    lda enabled,x
    beq :-
    stx sel
    jsr menu_changed
@no_up:
    lda pad_new
    and #PAD_DOWN
    beq @no_down
    ldx sel
:   inx
    txa
    and #3
    tax
    lda enabled,x
    beq :-
    stx sel
    jsr menu_changed
@no_down:
    lda pad_new
    and #PAD_SELECT
    beq @no_select
    ldx mode
    inx
    cpx #3
    bcc :+
    ldx #0
:   stx mode
    jsr menu_changed
@no_select:
    lda pad_new
    and #PAD_A
    beq @no_a
    ldx sel
    lda enabled,x
    beq @no_a
    txa
    jmp start_title
@no_a:
    lda at_end
    beq @feed
    ldx #MENU_ID            ; 背景は繰り返し
    jmp stream_start
@feed:
    lda ready               ; スプライトを書き直した NMI ではパケットが残っている
    bne @done
    jsr maybe_audio_start
    jsr build_packet
@done:
    jmp clock_step

menu_changed:
    jsr build_oam
    lda #1
    sta oam_dirty
    rts

; A = タイトル番号
start_title:
    tax
    lda #1
    sta state
    jmp stream_start

; ---- 再生中 -----------------------------------------------------------------
play_frame:
    lda pad_new
    and #PAD_START
    beq @no_start
    jmp go_menu
@no_start:
    lda pad_new
    and #PAD_SELECT
    beq @no_select
    lda fps_on
    eor #1
    sta fps_on
    jsr update_mask
@no_select:
    lda pad_new
    and #PAD_B
    beq @no_b
    lda at_end
    beq :+
    ldx stream              ; 最後で止まっているときは最初から
    jmp stream_start
:   lda paused
    eor #1
    sta paused
    lda #0
    sta stepping
    jsr audio_stop
@no_b:
    lda paused
    beq @no_step
    lda pad_new
    and #PAD_RIGHT
    beq :+
    lda at_end
    bne :+
    lda #1
    sta stepping
:   lda pad_new
    and #PAD_LEFT
    beq @no_step
    jmp step_back
@no_step:
    ; 最後まで来たとき
    lda at_end
    beq @feed
    lda state
    cmp #2
    bne :+
    jmp go_menu             ; 起動時のオープニングは終わったらメニューへ
:   lda paused
    bne @fps
    lda mode
    cmp #MODE_STOP
    bne :+
    lda #1
    sta paused
    jsr audio_stop
    jmp @fps
:   cmp #MODE_SEQ
    beq @next_title
    ldx stream              ; REPEAT
    jmp stream_start
@next_title:
    ldx stream
@nt:
    inx
    cpx #NTITLES
    bcc :+
    jmp go_menu             ; 全部見終わったらメニューへ
:
    lda enabled,x
    beq @nt
    jmp stream_start
@feed:
    lda paused
    beq @build
    lda stepping
    beq @fps
    jsr build_packet
    lda last_term
    and #$08
    beq @fps
    lda #0
    sta stepping
    jmp @fps
@build:
    jsr maybe_audio_start
    jsr build_packet
@fps:
    ; 1 秒ごとに「その 1 秒で終わったコマ数」を表示する
    inc sec_tick
    lda sec_tick
    cmp #60
    bcc @done
    lda #0
    sta sec_tick
    lda fps_count
    ldx #0
    stx fps_count
@ten:
    cmp #10
    bcc @one
    sbc #10
    inx
    bne @ten
@one:
    sta patch_d2
    txa
    bne :+
    lda #TILE_BLANK
:   sta patch_d1
    lda #1
    sta patch_n
@done:
    jmp clock_step

go_menu:
    lda #0
    sta state
    ldx #MENU_ID
    jmp stream_start

; -----------------------------------------------------------------------------
; X = 動画の番号。画面を消して PPU をきれいにし、その動画の先頭から読む
stream_start:
    stx stream
    jsr audio_stop
    lda #1
    sta ppu_busy
    lda #0
    sta ready
    sta at_end
    sta paused
    sta stepping
    sta frames
    sta frames+1
    sta sec_tick
    sta fps_count
    sta oam_dirty
    sta patch_n
    sta last_term
    sta acc
    sta acc+1
    sta acc+2
    sta aslot
    sta built
    sta PPUMASK
    bit PPUSTATUS
    lda #$00                ; パターンテーブル $0000-$0FFF
    ldx #$10
    jsr fill_vram
    lda #$20                ; ネームテーブル $2000-$23FF
    ldx #$04
    jsr fill_vram
    ldx stream
    jsr read_entry
    lda #$3F                ; パレット: BG は動画ごと（目録）、スプライトは共通
    sta PPUADDR
    lda #$00
    sta PPUADDR
    ldx #0
@bgpal:
    lda ent+E_PAL,x
    sta PPUDATA
    inx
    cpx #16
    bne @bgpal
@pal:
    lda palette,x
    sta PPUDATA
    inx
    cpx #32
    bne @pal
    lda #$00
    sta PPUADDR
    sta PPUADDR

    jsr build_oam
    lda #0
    sta OAMADDR
    lda #>OAM
    sta OAMDMA

    lda ent+5
    sta audio_ok
    lda ent+1
    sta bank
    jsr set_bank
    lda #$00
    sta vp
    lda #$80
    sta vp+1
    jsr update_mask
    lda #0
    sta ppu_busy
    rts

update_mask:
    lda state
    beq @all
    lda fps_on
    bne @all
    lda #MASK_PLAY
    sta mask
    rts
@all:
    lda #MASK_ALL
    sta mask
    rts

; A = アドレス上位、X = ページ数（256 バイト単位）。0 で埋める
fill_vram:
    sta PPUADDR
    lda #0
    sta PPUADDR
    ldy #0
@lp:
    sta PPUDATA
    iny
    bne @lp
    dex
    bne @lp
    rts

; バンク切り替え（バスの衝突を避けるため、同じ値が入った ROM の場所へ書く）
set_bank:
    lda bank
select_bank_a:
    tay
    sta banktable,y
    rts

; X = 番号 → 目録の 1 件を ent へ（バンク 0 に切り替えたまま戻る）
read_entry:
    lda #0
    jsr select_bank_a
    txa
    asl a
    asl a
    asl a
    asl a
    sta tmp                 ; 16*番号
    asl a
    clc
    adc tmp                 ; 48*番号
    tay
    ldx #0
@lp:
    lda CAT_TITLES,y
    sta ent,x
    iny
    inx
    cpx #ENT_SIZE
    bne @lp
    rts

; -----------------------------------------------------------------------------
; 次のパケットを読み、NMI 用バッファに並べる
;   ROM: k（bit7 = 絵 16 バイト）, { 番号, 絵 8/16 } × k,
;        ネームテーブル（「前に書き終えた場所から何マス先か」で位置を表す）:
;          $00-$7F s, v      : s マス先の 1 マスに v
;          $80-$BF s, v...   : s マス先から (下位 6bit + 2) マスに v...
;          $C0 s             : s マス進むだけ
;          $C1 a b / $C2 x16 : パレット $3F00 から 2 / 16 バイト
;        終端 $F0 | フラグ
;   バッファ = k, { 番号, 絵 } × k, { 上位, 下位, 飛び先-1 (下位), 飛び先-1 (上位), データ... } 終端
build_packet:
    ldx #0
    ldy #0
    lda (vp),y
    sta BUF,x
    inx
    iny
    sta tmp
    and #$7F
    beq @nt
    sta tmp3                ; 枚数
    lda #9
    bit tmp
    bpl :+
    lda #17
:   sta tmp4                ; 1 枚のバイト数
@up:
    lda tmp4
    sta tmp2
@up_byte:
    lda (vp),y
    sta BUF,x
    inx
    iny
    dec tmp2
    bne @up_byte
    dec tmp3
    bne @up
@nt:
    lda #$00
    sta naddr
    lda #$20
    sta naddr+1
@run:
    lda (vp),y
    cmp #$F0
    bcc :+
    jmp @term
:   iny
    cmp #$80
    bcc @single
    cmp #$C0
    bcs @special
    and #$3F                ; ラン
    adc #2                  ; （C = 0）
    sta tmp
    lda (vp),y
    iny
    jmp @skip_emit
@single:
    pha
    lda #1
    sta tmp
    pla
@skip_emit:
    clc
    adc naddr
    sta naddr
    sta eaddr
    lda naddr+1
    adc #0
    sta naddr+1
    sta eaddr+1
    lda naddr               ; 書き終えた場所へ
    clc
    adc tmp
    sta naddr
    bcc @emit
    inc naddr+1
    bne @emit
@special:
    cmp #OP_SKIP
    bne @pal
    lda (vp),y
    iny
    clc
    adc naddr
    sta naddr
    bcc @run
    inc naddr+1
    bne @run
@pal:
    cmp #OP_PAL2
    beq :+
    lda #16
    bne :++
:   lda #2
:   sta tmp
    lda #$00
    sta eaddr
    lda #$3F
    sta eaddr+1
@emit:                      ; eaddr から tmp バイト
    lda eaddr+1
    sta BUF,x
    inx
    lda eaddr
    sta BUF,x
    inx
    lda tmp
    asl a
    asl a                   ; 4n（展開ループの 1 バイト分は 4 バイト）
    sta tmp2
    lda #<(chain_end - 1)
    sec
    sbc tmp2
    sta BUF,x
    inx
    lda #>(chain_end - 1)
    sbc #0
    sta BUF,x
    inx
@copy:
    lda (vp),y
    sta BUF,x
    inx
    iny
    dec tmp
    bne @copy
    jmp @run
@term:
    sta BUF,x
    sta last_term
    iny
    tya
    clc
    adc vp
    sta vp
    bcc :+
    inc vp+1
:
    lda last_term
    and #$08
    beq @no_fe
    inc frames
    bne :+
    inc frames+1
:   inc fps_count
@no_fe:
    lda last_term
    and #$03
    beq @done
    cmp #1
    bne @end
    inc bank
    jsr set_bank
    lda #$00
    sta vp
    lda #$80
    sta vp+1
    jmp @done
@end:
    lda #1
    sta at_end
@done:
    lda #1
    sta ready
    sta built
    rts

; -----------------------------------------------------------------------------
; ← コマ戻し: 少し前のスナップショットを PPU に書き、目的のコマまで早送りする。
; その間（最大 0.4 秒ほど）は画面を消す。
step_back:
    ; 戻り先 = 「今見えているコマ」の 1 つ前が終わった時点
    lda frames
    sta target
    lda frames+1
    sta target+1
    lda last_term
    and #$08
    beq :+
    lda target
    bne @dec
    dec target+1
@dec:
    dec target
:   lda target              ; 1 コマ目より前には戻らない
    ora target+1
    bne :+
    rts
:
    ; スナップショットを選ぶ（コマ数が target 以下で一番新しいもの）
    ldx stream
    jsr read_entry          ; バンク 0 のまま
    lda ent+3
    sta ptr
    lda ent+4
    sta ptr+1
    lda ent+2
    sta tmp                 ; 件数
@find:
    ldy #7                  ; この件のコマ数 > target なら終わり
    lda (ptr),y
    cmp target+1
    bcc @take
    bne @found
    dey
    lda (ptr),y
    cmp target
    beq @take
    bcs @found
@take:
    ldy #15
:   lda (ptr),y
    sta snap,y
    dey
    bpl :-
    lda ptr
    clc
    adc #16
    sta ptr
    bcc :+
    inc ptr+1
:   dec tmp
    bne @find
@found:
    ; 書き込み中は NMI を止める（スタックを借りるため）
    lda #1
    sta ppu_busy
    lda #0
    sta PPUCTRL
    sta PPUMASK
    sta ready
    lda snap
    jsr select_bank_a
    lda snap+1
    sta ptr
    lda snap+2
    sta ptr+1
    bit PPUSTATUS
    lda ent+E_MODE
    beq @mono
    jmp @two
@mono:
    ; ネームテーブル 960 バイト
    lda #$20
    sta PPUADDR
    lda #$00
    sta PPUADDR
    lda #<960
    sta cnt
    lda #>960
    sta cnt+1
    jsr copy_cnt
    ; 絵 256 枚 × 8 バイト（plane0）
    ldx #0
@pat:
    lda slot_hi,x
    sta PPUADDR
    lda slot_lo,x
    sta PPUADDR
    ldy #0
:   lda (ptr),y
    sta PPUDATA
    iny
    cpy #8
    bne :-
    lda ptr
    clc
    adc #8
    sta ptr
    bcc :+
    inc ptr+1
:   inx
    bne @pat
    ; 白黒反転
    lda #$3F
    sta PPUADDR
    lda #$00
    sta PPUADDR
    ldy #0
    lda (ptr),y
    beq @normal
    lda #$30
    sta PPUDATA
    lda #$0F
    sta PPUDATA
    jmp @pal_done
@two:                       ; ネームテーブル + 属性 1024、CHR 4096、パレット 16
    lda #$20
    ldx #$04
    jsr copy_pages
    lda #$00
    ldx #$10
    jsr copy_pages
    lda #$3F
    sta PPUADDR
    lda #$00
    sta PPUADDR
    lda #16
    sta cnt
    lda #0
    sta cnt+1
    jsr copy_cnt
    jmp @pal_done
@normal:
    lda #$0F
    sta PPUDATA
    lda #$30
    sta PPUDATA
@pal_done:
    ; 続きのパケットの位置とコマ数
    lda snap+3
    sta bank
    jsr set_bank
    lda snap+4
    sta vp
    lda snap+5
    sta vp+1
    lda snap+6
    sta frames
    lda snap+7
    sta frames+1
    lda #$88                ; 「コマの終わり」の直後にいることにする
    sta last_term
    lda snap+8              ; 音声時計もその時点に
    sta acc
    lda snap+9
    sta acc+1
    lda snap+10
    sta acc+2
    lda snap+11
    sta aslot
@ff:
    lda frames
    cmp target
    bne @ff_step
    lda frames+1
    cmp target+1
    beq @ff_done
@ff_step:
    jsr build_packet
    jsr flush
    jsr clock_advance
    jmp @ff
@ff_done:
    lda #0
    sta built
    sta ready
    sta at_end
    sta stepping
    sta fps_count
    lda #$00
    sta PPUADDR
    sta PPUADDR
    lda #CTRL_VALUE
    sta PPUCTRL
    lda #0
    sta ppu_busy
    rts

; A = PPU アドレス上位、X = ページ数。ptr から写す（ptr は進む）
copy_pages:
    sta PPUADDR
    lda #0
    sta PPUADDR
    ldy #0
@lp:
    lda (ptr),y
    sta PPUDATA
    iny
    bne @lp
    inc ptr+1
    dex
    bne @lp
    rts

; ptr から cnt バイトを PPUDATA へ（ptr は進む）
copy_cnt:
    ldy #0
@lp:
    lda (ptr),y
    sta PPUDATA
    inc ptr
    bne :+
    inc ptr+1
:   lda cnt
    bne :+
    dec cnt+1
:   dec cnt
    lda cnt
    ora cnt+1
    bne @lp
    rts

; -----------------------------------------------------------------------------
; スプライトを作り直す（OAM バッファ $0200）
build_oam:
    ldx #0
    lda #$FF
@clr:
    sta OAM,x
    inx
    bne @clr
    lda #0
    sta oam_i
    lda state
    bne @play
    ; メニュー
    lda #<str_head
    ldy #>str_head
    ldx #3
    jsr put_str_at          ; 見出し
    lda #0
    sta tmp2                ; タイトル番号
@title:
    ldx tmp2
    lda enabled,x           ; 入っていない動画は名前を出さない
    beq @next_t
    lda #0                  ; 色: 0=白 / 1=黄（選択中）
    cpx sel
    bne :+
    lda #1
:   sta tattr
    lda #16
    sta tx
    txa
    asl a
    asl a
    asl a
    asl a
    adc #63                 ; y = 64 + 番号*16（OAM には y-1 を書く）
    sta ty
    lda title_lo,x
    sta ptr
    lda title_hi,x
    sta ptr+1
    jsr put_str
@next_t:
    inc tmp2
    lda tmp2
    cmp #NTITLES
    bne @title
    lda #<str_mode
    ldy #>str_mode
    ldx #4
    jsr put_str_at
    ldx mode
    lda mode_lo,x
    sta ptr
    lda mode_hi,x
    sta ptr+1
    lda #16
    sta tx
    lda #167
    sta ty
    lda #0
    sta tattr
    jsr put_str
    rts
@play:
    ; 再生中: 左上に「FPS」と数字 2 つ（数字は NMI で書き換える）
    lda #<str_fps
    ldy #>str_fps
    ldx #5
    jsr put_str_at
    lda #TILE_BLANK
    sta OAM + FPS_SPR*4 + 1
    sta OAM + FPS_SPR*4 + 5
    lda #15
    sta OAM + FPS_SPR*4
    sta OAM + FPS_SPR*4 + 4
    lda #3
    sta OAM + FPS_SPR*4 + 2
    sta OAM + FPS_SPR*4 + 6
    lda #48
    sta OAM + FPS_SPR*4 + 3
    lda #56
    sta OAM + FPS_SPR*4 + 7
    lda #(FPS_SPR + 2) * 4
    sta oam_i
    rts

; A/Y = 位置つき文字列（x, y-1, 色, 文字..., STR_END）
put_str_at:
    sta ptr
    sty ptr+1
    ldy #0
    lda (ptr),y
    sta tx
    iny
    lda (ptr),y
    sta ty
    iny
    lda (ptr),y
    sta tattr
    lda ptr
    clc
    adc #3
    sta ptr
    bcc put_str
    inc ptr+1
; ptr = 文字列（STR_END で終わり）、tx, ty, tattr
put_str:
    ldy #0
@lp:
    lda (ptr),y
    cmp #STR_END
    beq @done
    cmp #$FF
    beq @adv
    ldx oam_i
    sta OAM+1,x
    lda ty
    sta OAM,x
    lda tattr
    sta OAM+2,x
    lda tx
    sta OAM+3,x
    txa
    clc
    adc #4
    sta oam_i
@adv:
    lda tx
    clc
    adc #8
    sta tx
    iny
    bne @lp
@done:
    rts

; -----------------------------------------------------------------------------
; DMC が鳴っていると、まれにパッドの読み取りが 1 bit ずれるので、2 回同じ値になるまで読む
readpad:
    lda pad
    sta pad_prev
@again:
    jsr read_once
    sta pad
    jsr read_once
    cmp pad
    bne @again
    lda pad_prev
    eor #$FF
    and pad
    sta pad_new
    rts

read_once:
    lda #1
    sta JOY1
    sta tmp2
    lsr a
    sta JOY1
@lp:
    lda JOY1
    lsr a
    rol tmp2
    bcc @lp
    lda tmp2
    rts

; -----------------------------------------------------------------------------
; 音声
; -----------------------------------------------------------------------------
audio_stop:
    lda #0
    sta dmc_pending
    sta APU_STATUS
    sta audio_on
    sta need_write
    rts

; 音声があって止まっていれば、次に作るパケットと同時に鳴り始めるよう準備する
maybe_audio_start:
    lda audio_ok
    beq @r
    lda audio_on
    ora dmc_pending
    bne @r
    jmp audio_start
@r:
    rts

; 音声時計（acc, aslot）の位置から鳴らす。途中からは 64 バイト単位
audio_start:
    lda acc
    sta tclk
    lda acc+1
    sta tclk+1
    lda acc+2
    sta tclk+2
    ldx #0
@k:
    cpx #7
    bcs @kdone
    lda tclk
    cmp ent+E_CHUNK2
    lda tclk+1
    sbc ent+E_CHUNK2+1
    lda tclk+2
    sbc ent+E_CHUNK2+2
    bcc @kdone
    lda tclk
    sbc ent+E_CHUNK2
    sta tclk
    lda tclk+1
    sbc ent+E_CHUNK2+1
    sta tclk+1
    lda tclk+2
    sbc ent+E_CHUNK2+2
    sta tclk+2
    inx
    bne @k
@kdone:
    stx tmp                 ; k（0-7）
    ; その位置の開始レベル: 置き場の先頭 + 512*slot + 497 + k
    lda aslot
    asl a
    adc ent+E_LVLHI
    sta ptr+1
    lda #LEVELS_LO
    clc
    adc tmp
    sta ptr
    ldy #0
    lda (ptr),y
    sta DMC_RAW
    lda ent+E_DMCCTRL
    sta DMC_FREQ
    ldx aslot
    jsr slot_reg_a
    clc
    adc tmp
    sta DMC_START
    lda tmp
    asl a
    asl a
    sta tmp
    lda #AUDIO_L
    sec
    sbc tmp
    sta DMC_LEN
    jsr next_slot_x
    jsr slot_reg_a
    sta nx4012
    lda #0
    sta need_write
    lda #1
    sta dmc_pending
    rts

; X = aslot の次のスロット
next_slot_x:
    ldx aslot
    inx
    cpx ent+E_NSLOTS
    bcc :+
    ldx #0
:   rts

; X = スロット → A = その $4012 の値
slot_reg_a:
    txa
    asl a
    asl a
    asl a
    clc
    adc ent+E_SLOTREG
    rts

; 1 フレーム分、時計を進める（鳴っていればセグメントの切り替えも面倒を見る）
clock_step:
    lda audio_on
    ora dmc_pending
    bne audio_tick
    lda built
    beq @r
    lda #0
    sta built
    jmp clock_advance
@r:
    rts

audio_tick:
    lda #0
    sta built
    jsr clock_advance
    lda need_write
    beq @r
    lda acc                 ; セグメントが変わってから MARGIN2 経ったか
    cmp #<MARGIN2
    lda acc+1
    sbc #>MARGIN2
    lda acc+2
    sbc #^MARGIN2
    bcc @r
    lda #0
    sta need_write
    jsr next_slot_x         ; 今鳴っているセグメントの次を書いておく
    jsr slot_reg_a
    sta DMC_START
    lda #AUDIO_L
    sta DMC_LEN
@r:
    rts

clock_advance:
    clc
    lda acc
    adc #<FRAME2
    sta acc
    lda acc+1
    adc #>FRAME2
    sta acc+1
    lda acc+2
    adc #^FRAME2
    sta acc+2
    lda acc
    cmp ent+E_SEG2
    lda acc+1
    sbc ent+E_SEG2+1
    lda acc+2
    sbc ent+E_SEG2+2
    bcc @r
    lda acc
    sbc ent+E_SEG2
    sta acc
    lda acc+1
    sbc ent+E_SEG2+1
    sta acc+1
    lda acc+2
    sbc ent+E_SEG2+2
    sta acc+2
    jsr next_slot_x
    stx aslot
    lda #1
    sta need_write
@r:
    rts

; -----------------------------------------------------------------------------
nmi:
    pha
    txa
    pha
    tya
    pha
    lda ppu_busy
    bne nmi_skip
    lda oam_dirty
    beq @no_dma
    lda #0                  ; スプライトを書き直す（この回はパケットを流さない）
    sta OAMADDR
    lda #>OAM
    sta OAMDMA
    lda #0
    sta oam_dirty
    beq nmi_no_buf
@no_dma:
    lda patch_n
    beq @no_patch
    lda #FPS_SPR*4 + 1
    sta OAMADDR
    lda patch_d1
    sta OAMDATA
    lda #FPS_SPR*4 + 5
    sta OAMADDR
    lda patch_d2
    sta OAMDATA
    lda #0
    sta OAMADDR
    sta patch_n
@no_patch:
    lda ready
    beq nmi_no_buf
    jsr flush
    lda #0
    sta ready
    lda dmc_pending         ; このパケットと同時に音を鳴らし始める
    beq nmi_no_buf
    lda #$10
    sta APU_STATUS
    lda nx4012              ; 今のセグメントの次を先に書いておく
    sta DMC_START
    lda #AUDIO_L
    sta DMC_LEN
    lda #0
    sta dmc_pending
    lda #1
    sta audio_on
nmi_no_buf:
    lda #0
    sta PPUSCROLL
    sta PPUSCROLL
    lda #CTRL_VALUE
    sta PPUCTRL
    lda mask
    sta PPUMASK
nmi_skip:
    inc nmi_count
    pla
    tay
    pla
    tax
    pla
irq:
    rti

; バッファの中身を PPU に流す（NMI から、または NMI を止めた状態でメインから呼ぶ）
flush:
    tsx
    stx saved_s
    ldx #$FF                ; 次の pla で $0100 を読む
    txs
    bit PPUSTATUS
    ; --- タイルの絵（1 枚 = タイル番号 + 8 バイト） ---
    pla
    bne :+
    jmp run_loop
:   bmi up16_start
    tay
up_loop:
    pla
    tax
    lda slot_hi,x
    sta PPUADDR
    lda slot_lo,x
    sta PPUADDR
.repeat 8
    pla
    sta PPUDATA
.endrepeat
    dey
    bne up_loop
    jmp run_loop
up16_start:                 ; 4 段階の絵（16 バイト = 2 面）
    and #$7F
    tay
up16_loop:
    pla
    tax
    lda slot_hi,x
    sta PPUADDR
    lda slot_lo,x
    sta PPUADDR
.repeat 16
    pla
    sta PPUDATA
.endrepeat
    dey
    bne up16_loop
    jmp run_loop
buf_done:
    ldx saved_s
    txs
    rts
run_loop:
    pla
    bmi buf_done
    sta PPUADDR
    pla
    sta PPUADDR
    rts                     ; バッファに置いた「飛び先-1」へ（展開ループの途中に入る）
chain:
.repeat MAX_RUN
    pla
    sta PPUDATA
.endrepeat
chain_end:
    jmp run_loop

; -----------------------------------------------------------------------------
.segment "RODATA"
palette:
    .byte $0F, $30, $0F, $30,  $0F, $30, $0F, $30,  $0F, $30, $0F, $30,  $0F, $30, $0F, $30
    .byte $0F, $30, $10, $0F,  $0F, $28, $10, $0F,  $0F, $00, $10, $0F,  $0F, $21, $10, $0F

; 位置つき文字列: x, y-1, 色, 文字..., STR_END
str_head:  .byte 16, 39, 3
           .byte "MOVIES", STR_END
str_mode:  .byte 16, 151, 3
           .byte "MODE", STR_END
str_fps:   .byte 16, 15, 3
           .byte "FPS", STR_END

t0: .byte "0 OPENING", STR_END
t1: .byte "1 ORIGEN", STR_END
t2: .byte "2 TK REMIX", STR_END
t3: .byte "3 2026AI", STR_END
title_lo: .byte <t0, <t1, <t2, <t3
title_hi: .byte >t0, >t1, >t2, >t3

m0: .byte "REPEAT", STR_END
m1: .byte "STOP", STR_END
m2: .byte "SEQ", STR_END
mode_lo: .byte <m0, <m1, <m2
mode_hi: .byte >m0, >m1, >m2

; タイル番号 → パターンテーブルのアドレス（plane0）
slot_hi:
.repeat 256, i
    .byte >(i * 16)
.endrepeat
slot_lo:
.repeat 256, i
    .byte <(i * 16)
.endrepeat


.segment "BANKTABLE"
banktable:
.repeat 256, i
    .byte i
.endrepeat

.segment "VECTORS"
    .word nmi, reset, irq
