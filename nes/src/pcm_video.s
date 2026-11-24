; =============================================================================
; pcm_video.s - $4011 PCM で音を鳴らす動画プレイヤー（配布用サンプル）
;
; CPU が 96 サイクルごとに 1 回 $4011 へ書く（18,643Hz）。NMI・DMC・IRQ は使わない。
; 再生中のすべての処理を 96 サイクルの「枠」に分け、どの道を通っても枠の長さが変わらないようにしてある。
; 枠の中の数字は「直前の $4011 への書き込みからのサイクル数」（96 で次の書き込み）。
;
; 1 フレームの流れ（再生中）
;   描画中の枠（r_loop）… 次のレコードを $0100- に写す → VBlank 中の音を先読み → スプライト 0 ヒットを待つ
;   ヒット（239 ライン）→ 待ち 1 枠 → VBlank 中の書き込み枠 → 仕上げ 1 枠 → 後処理 9 枠 → 描画中の枠へ
; ボタンでメニューの操作・一時停止などをするときは、この流れを抜けて（音を止めて）普通の処理をする。
;
; ROM の形は tools/video/make_pcm_rom.py の説明を参照（定数は同じ値にすること）
; =============================================================================
.include "nes.inc"

NV         = 19             ; VBlank 中の書き込み枠の最大
BUF        = $0100          ; レコードの置き場（$0100-$01EA）。スタックは $01EB-$01FF
T_OFF      = 12 * NV + 6    ; レコードの先頭（count）を置く位置（BUF からの距離）
HDR_COUNT  = BUF + T_OFF
HDR_FLAGS  = BUF + T_OFF - 1
OAM        = $0200
ABUF       = $0300          ; VBlank 中に鳴らす音（先読み）
SILENCE    = $0400          ; 音のない動画のときに読む無音（$0400-$06FF）
DMC_RAW    = $4011
APU_STATUS = $4015
F_FRAME_END = 1
F_END       = 2
F_NEXT_BANK = 4
RES_TILE   = $FF            ; 右下に固定するタイル（plane1 = $FF。白黒では plane0 に本当の絵、パレット 2/3 = 0/1 の色）
SPR0_Y     = 238            ; スプライト 0（239 ラインに表示）
SPR0_X     = 248
AUDIO_LEAD = 334            ; make_pcm_rom.py と同じ
SIL_VALUE  = 50             ; 無音の値（make_pcm_rom.py の silence_value と同じ）
CTRL_TIMED = %00101000      ; NMI なし / BG $0000 / スプライト 8x16（文字はタイル番号が奇数 → $1000 側）
MASK_ON    = MASK_BG | MASK_BG_LEFT | MASK_SPR | MASK_SPR_LEFT
TILE_BLANK = 2 * 40 + 1     ; 文字 c のスプライトのタイル番号は 2c+1（8x16）
FPS_OAM    = 248            ; FPS の数字 = スプライト 62, 63
CAT        = $8010          ; 目録（バンク 0）: 1 件 16 バイト
FONT_ADDR  = $8100
NTITLES    = 4
MENU_ID    = 4
MODE_REPEAT = 0
MODE_STOP   = 1
MODE_SEQ    = 2
STR_END    = $FE

; ---- 文字 → スプライトのタイル番号（tools/video/gen_font.py と合わせる）
.charmap '0', 1
.charmap '1', 3
.charmap '2', 5
.charmap '3', 7
.charmap '4', 9
.charmap '5', 11
.charmap '6', 13
.charmap '7', 15
.charmap '8', 17
.charmap '9', 19
.charmap 'A', 21
.charmap 'B', 23
.charmap 'C', 25
.charmap 'D', 27
.charmap 'E', 29
.charmap 'F', 31
.charmap 'G', 33
.charmap 'H', 35
.charmap 'I', 37
.charmap 'J', 39
.charmap 'K', 41
.charmap 'L', 43
.charmap 'M', 45
.charmap 'N', 47
.charmap 'O', 49
.charmap 'P', 51
.charmap 'Q', 53
.charmap 'R', 55
.charmap 'S', 57
.charmap 'T', 59
.charmap 'U', 61
.charmap 'V', 63
.charmap 'W', 65
.charmap 'X', 67
.charmap 'Y', 69
.charmap 'Z', 71
.charmap '-', 73
.charmap ':', 75
.charmap '>', 77
.charmap '/', 79
.charmap ' ', $FF

.segment "ZEROPAGE"
aptr:       .res 2      ; 音声のページ（下位は常に 0）
ay:         .res 1      ; 音声のページ内の位置
vptr:       .res 2      ; レコードのページ（下位は常に 0）
vy:         .res 1
vrem:       .res 1      ; 描画中に写す残りバイト数
vdst:       .res 1      ; 写す先（BUF からの距離。下へ向かって写す）
bptr:       .res 2      ; 先読みする音声のページ
by:         .res 1
prem:       .res 1      ; 先読みの残り
pdst:       .res 1      ; 先読みの書き先（ABUF からの距離）
py:         .res 1      ; 前のヒットのときの音声の位置
ppage:      .res 1
dsave:      .res 1      ; 先読みとのずれ（0 か 1）
cnt1:       .res 1      ; 次に書くレコードの count + 1
ctmp:       .res 1
s_val:      .res 1      ; 書き込み前に S に入れる値
saved_s:    .res 1
guard:      .res 1      ; ヒットを見始めるまでの枠数
rcount:     .res 1      ; ヒットを待つ回数の上限
swflag:     .res 1
dtmp:       .res 1
kcur:       .res 1      ; 今のバンクの K
bank:       .res 1
pad:        .res 1
pad_prev:   .res 1
pad_new:    .res 1
evmask:     .res 1      ; 再生を抜けて処理するボタン
fps_show:   .res 1      ; 1 = FPS を出す（再生中で fps_on のとき）
silent:     .res 1      ; 1 = 音のない動画（RAM の無音を読む）
fin_vec:    .res 2      ; 仕上げの枠の次
frames:     .res 2      ; 今の動画で終わったコマ数
fps_count:  .res 1
sec_tick:   .res 1
d1:         .res 1      ; FPS の数字（タイル）
d2:         .res 1
fps_on:     .res 1
state:      .res 1      ; 0 = メニュー / 1 = 再生中 / 2 = 電源を入れた直後のオープニング
stream:     .res 1
sel:        .res 1
mode:       .res 1
at_end:     .res 1
target:     .res 2
tmp:        .res 1
tmp2:       .res 1
ptr:        .res 2
cnt:        .res 2
oam_i:      .res 1
tx:         .res 1
ty:         .res 1
tattr:      .res 1
enabled:    .res NTITLES
ent:        .res 12     ; 目録の 1 件: +0 入っている +1 最初のバンク +2 音なし +3 スナップショット数
                        ;   +4,+5 表の場所 +6 4 色 +8..+11 BG パレット

; ---- n サイクル待つ（n >= 2）-------------------------------------------------------
;   14 以上は「NOP の列の途中へ jsr し、最後の rts で戻る」（jsr 6 + NOP 2k + rts 6）。
;   分岐を使わないので、どこに置いてもサイクル数が変わらない。スタックを 2 バイト使う
.macro delay n
  .if (n) < 2
    .error "delay は 2 サイクル以上"
  .endif
  .if (n) >= 14
    .if ((n) .mod 2) = 1
      bit $00
      delay ((n) - 3)
    .else
      .if (n) > 60
        .error "delay が長すぎる（NOP の列を増やす）"
      .endif
      jsr sled_end - ((n) - 12) / 2
    .endif
  .elseif ((n) .mod 2) = 1
    bit $00
    .repeat ((n) - 3) / 2
      nop
    .endrepeat
  .else
    .repeat (n) / 2
      nop
    .endrepeat
  .endif
.endmacro

; ---- ページをまたがない分岐（またぐと 1 サイクル増えるので、リンク時に止める） ----
.macro BEQ_T t
    beq t
    .assert >* = >(t), error, "分岐がページをまたぐ"
.endmacro
.macro BNE_T t
    bne t
    .assert >* = >(t), error, "分岐がページをまたぐ"
.endmacro
.macro BCC_T t
    bcc t
    .assert >* = >(t), error, "分岐がページをまたぐ"
.endmacro
.macro BCS_T t
    bcs t
    .assert >* = >(t), error, "分岐がページをまたぐ"
.endmacro
.macro BVS_T t
    bvs t
    .assert >* = >(t), error, "分岐がページをまたぐ"
.endmacro

; ---- 音声を 1 つ読む（A に入る。23 サイクル、Y を壊す） --------------------------
.macro AREAD
    ldy ay              ; 3
    lda (aptr),y        ; 8
    iny                 ; 10
    BNE_T :+            ; 13 / 12
    inc aptr+1          ; 17
    jmp :++             ; 20
:   delay 7             ; 20
:   sty ay              ; 23
.endmacro

; ---- 音声の枠の頭: 読んで書く（書き込みは 27 サイクル目） ------------------------
.macro ASLOT
    AREAD               ; 23
    sta DMC_RAW         ; 27
.endmacro

; ---- VBlank 中の書き込み枠（音 10 + 書き込み 10 回 80 + rts 6 = 96） ----------------
;   引数: 書き込み先の並び（6 = PPUADDR, 7 = PPUDATA）
.macro W1 r
    pla
  .if (r) = 6
    sta PPUADDR
  .else
    sta PPUDATA
  .endif
.endmacro
.macro WSLOT r0, r1, r2, r3, r4, r5, r6, r7, r8, r9
    lda ABUF,x          ; 4
    sta DMC_RAW         ; 8 → 書き込み
    inx                 ; 10
    W1 r0
    W1 r1
    W1 r2
    W1 r3
    W1 r4
    W1 r5
    W1 r6
    W1 r7
    W1 r8
    W1 r9               ; 90
    rts                 ; 96
.endmacro

.segment "CODE"

; =============================================================================
; 描画中の枠（1 枠 = 27 + 66 + 3 = 96）
; =============================================================================
r_loop:
    ASLOT                   ; 0（書き込み）
    lda vrem                ; 3
    BEQ_T r_nocopy          ; 5 / 6
    ldy vy                  ; 8
    lda (vptr),y            ; 13
    iny                     ; 15
    BNE_T :+                ; 18 / 17
    inc vptr+1              ; 22
    jmp :++                 ; 25
:   delay 7                 ; 25
:   sty vy                  ; 28
    ldx vdst                ; 31
    sta BUF,x               ; 36
    dex                     ; 38
    stx vdst                ; 41
    dec vrem                ; 46
    delay 20                ; 66
    jmp r_loop              ; 69（= 96 - 27）
r_nocopy:                   ; 6
    lda prem                ; 9
    BEQ_T r_idle            ; 11 / 12
    ldy by                  ; 14
    lda (bptr),y            ; 19
    ldx pdst                ; 22
    sta ABUF,x              ; 27
    inc pdst                ; 32
    iny                     ; 34
    BNE_T :+                ; 37 / 36
    inc bptr+1              ; 41
    jmp :++                 ; 44
:   delay 7                 ; 44
:   sty by                  ; 47
    dec prem                ; 52
    delay 14                ; 66
    jmp r_loop              ; 69
r_idle:                     ; 12
    lda guard               ; 15
    BEQ_T r_poll            ; 17 / 18
    dec guard               ; 22
    delay 44                ; 66
    jmp r_loop              ; 69
r_poll:                     ; 18
    bit PPUSTATUS           ; 22
    BVS_T r_hit             ; 24 / 25
    dec rcount              ; 29
    BEQ_T r_hit_late        ; 31 / 32
    delay 35                ; 66
    jmp r_loop              ; 69
r_hit_late:                 ; 32（ヒットが見つからない。ここで区切って続ける）
    jmp r_hit

; ---- ヒット → 待ち 1 枠 → 書き込み枠へ ------------------------------------------
r_hit:                      ; 25
    lda ay                  ; 28
    sec                     ; 30
    sbc py                  ; 33
    sec                     ; 35
    sbc #54                 ; 37   (P - P_prev - 310) の下位。0 か 1 のはず
    cmp #2                  ; 39
    BCC_T :+                ; 42 / 41
    lda #0                  ; 43
    jmp :++                 ; 46
:   delay 4                 ; 46
:   sta dsave               ; 49
    lda ay                  ; 52
    sta py                  ; 55
    lda aptr+1              ; 58
    sta ppage               ; 61
    delay 8                 ; 69
    ASLOT                   ; 0  待ち 1
    ; 音声の位置を、VBlank 中（書き込み count 枠 + 仕上げ 1 枠）に先読みから鳴らす分だけ先に進めておく
    lda ay                  ; 3
    clc                     ; 5
    adc cnt1                ; 8
    sta ay                  ; 11
    BCC_T :+                ; 14 / 13
    inc aptr+1              ; 18
    jmp :++                 ; 21
:   delay 7                 ; 21
:   delay 48                ; 69
    tsx                     ; 71
    stx saved_s             ; 74
    ldx s_val               ; 77
    txs                     ; 79
    ldx dsave               ; 82
    rts                     ; 88 → 書き込み枠の lda 4 + sta 4 で 96

; =============================================================================
; VBlank 中の書き込み枠（5 種類）と仕上げの枠。レコードの中の「飛び先-1」を rts で順にたどる
; =============================================================================
slot_T10:  WSLOT 6, 6, 7, 7, 7, 7, 7, 7, 7, 7
slot_T37:  WSLOT 6, 6, 7, 6, 6, 7, 7, 7, 7, 7
slot_T46:  WSLOT 6, 6, 7, 7, 6, 6, 7, 7, 7, 7
slot_T55:  WSLOT 6, 6, 7, 7, 7, 6, 6, 7, 7, 7
slot_T334: WSLOT 6, 6, 7, 6, 6, 7, 6, 6, 7, 7
fin:
    lda ABUF,x              ; 4
    sta DMC_RAW             ; 8 → 0
    ldx saved_s             ; 3
    txs                     ; 5
    lda #0                  ; 7
    sta PPUSCROLL           ; 11
    sta PPUSCROLL           ; 15
    lda #CTRL_TIMED         ; 17
    sta PPUCTRL             ; 21
    lda #FPS_OAM + 1        ; 23
    sta OAMADDR             ; 27
    lda d1                  ; 30
    sta OAMDATA             ; 34
    lda #FPS_OAM + 5        ; 36
    sta OAMADDR             ; 40
    lda d2                  ; 43
    sta OAMDATA             ; 47
    lda #0                  ; 49
    sta OAMADDR             ; 53
    delay 11                ; 64
    jmp (fin_vec)           ; 69（再生中は ps1、普通の処理から呼んだときは upl_ret）

; =============================================================================
; 後処理の枠（1 枠 = 27 + 69）
; =============================================================================
; 1: パッド 3 ビット
ps1:
    ASLOT
    delay 21                ; 21
    lda #1                  ; 23
    sta JOY1                ; 27
    lda #0                  ; 29
    sta JOY1                ; 33
.repeat 3
    lda JOY1                ; +4
    lsr a                   ; +2
    rol pad                 ; +5
.endrepeat                  ; 66
    delay 3                 ; 69
; 2: パッド 5 ビット / 押された瞬間
    ASLOT
.repeat 5
    lda JOY1
    lsr a
    rol pad
.endrepeat                  ; 55
    lda pad_prev            ; 58
    eor #$FF                ; 60
    and pad                 ; 63
    sta pad_new             ; 66
    delay 3                 ; 69
; 3: 動画の終わり（SELECT の FPS 切り替えは、再生を抜けてスプライトを書き直す: ev_play）
    ASLOT
    lda pad                 ; 3
    sta pad_prev            ; 6
    delay 24                ; 30
    lda HDR_FLAGS           ; 34
    and #F_END              ; 36
    BEQ_T :+                ; 39 / 38
    jmp ev_end              ;      （再生を抜ける）
:   delay 30                ; 69
; 4: 終わったコマ数
    ASLOT
    lda HDR_FLAGS           ; 4
    and #F_FRAME_END        ; 6
    tax                     ; 8
    clc                     ; 10
    adc fps_count           ; 13
    sta fps_count           ; 16
    txa                     ; 18
    clc                     ; 20
    adc frames              ; 23
    sta frames              ; 26
    lda frames+1            ; 29
    adc #0                  ; 31
    sta frames+1            ; 34
    delay 35                ; 69
; 5: 半秒ごとに FPS の数字（半秒に終わったコマ数 × 2 を出す。60fps の動画でも表の 0〜31 に収まる）
    ASLOT
    inc sec_tick            ; 5
    lda sec_tick            ; 8
    cmp #30                 ; 10
    BCS_T ps5_up            ; 13 / 12（分岐先をすぐ後ろに置いて、ページをまたがないように）
    delay 54                ; 66
    jmp ps6                 ; 69
ps5_up:                     ; 13
    ldx fps_count           ; 16
    lda fps_show            ; 19
    BNE_T :+                ; 22 / 21
    ldx #32                 ; 23
    jmp :++                 ; 26
:   delay 4                 ; 26
:   lda tens_tab,x          ; 30
    sta d1                  ; 33
    lda ones_tab,x          ; 37
    sta d2                  ; 40
    lda #0                  ; 42
    sta sec_tick            ; 45
    sta fps_count           ; 48
    delay 21                ; 69
ps6:
; 6: 次のバンクへ（音声の位置も新しいバンクに合わせる）
    ASLOT
    lda HDR_FLAGS           ; 4
    and #F_NEXT_BANK        ; 6
    sta swflag              ; 9
    BEQ_T ps6_no            ; 12 / 11
    ldx bank                ; 14
    inx                     ; 16
    stx bank                ; 19
    lda banktable,x         ; 23
    sta banktable,x         ; 28
    lda #$80                ; 30
    sta vptr+1              ; 33
    lda #1                  ; 35
    sta vy                  ; 38
    lda $8000               ; 42
    sec                     ; 44
    sbc kcur                ; 47
    sta dtmp                ; 50
    clc                     ; 52
    adc aptr+1              ; 55
    sta aptr+1              ; 58
    jmp ps6_end             ; 61
ps6_no:                     ; 12
    delay 49                ; 61
ps6_end:
    delay 8                 ; 69
; 7: 前のヒットの位置も新しいバンクに合わせる / 音のない動画は RAM の無音へ戻す
    ASLOT
    lda swflag              ; 3
    BEQ_T ps7_no            ; 5 / 6
    lda dtmp                ; 8
    clc                     ; 10
    adc ppage               ; 13
    sta ppage               ; 16
    lda $8000               ; 20
    sta kcur                ; 23
    jmp ps7_end             ; 26
ps7_no:                     ; 6
    delay 20                ; 26
ps7_end:
    lda silent              ; 29
    BEQ_T :+                ; 32 / 31
    lda #>SILENCE           ; 33
    sta aptr+1              ; 36
    sta ppage               ; 39
    jmp :++                 ; 42
:   delay 10                ; 42
:   delay 27                ; 69
; 8: 次のレコードの準備
ps8:
    ASLOT
    ldy vy                  ; 3
    lda (vptr),y            ; 8   次のレコードの count（読むだけ）
    tax                     ; 10
    stx ctmp                ; 13
    lda len_tab,x           ; 17
    sta vrem                ; 20
    lda #T_OFF              ; 22
    sta vdst                ; 25
    lda s_tab,x             ; 29
    sta s_val               ; 32
    inx                     ; 34
    stx cnt1                ; 37
    inx                     ; 39
    stx prem                ; 42
    lda #0                  ; 44
    sta pdst                ; 47
    delay 22                ; 69
; 9: 先読みの位置（前のヒットの位置 + 310 + 1）/ ヒットを見始めるまでの枠数 / ボタン
    ASLOT
    lda py                  ; 3
    clc                     ; 5
    adc #55                 ; 7
    sta by                  ; 10
    lda ppage               ; 13
    adc #1                  ; 15
    sta bptr+1              ; 18
    ldx ctmp                ; 21
    lda guard_tab,x         ; 25   次のレコードを写す・先読みする時間を引いた残り
    sec                     ; 27
    sbc HDR_COUNT           ; 31   今のフレームで書いた枠が多いほど、描画中の時間は短い
    sta guard               ; 34
    lda #60                 ; 36
    sta rcount              ; 39
    lda pad_new             ; 42
    and evmask              ; 45
    BEQ_T :+                ; 48 / 47
    jmp ev_button           ;      （再生を抜ける）
:   delay 18                ; 66
    jmp r_loop              ; 69

; =============================================================================
; ここからはサイクルを数えない処理
; =============================================================================
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
    jsr wait_vb
    txa
:   sta $0000,x
    sta $0200,x
    sta $0300,x
    inx
    bne :-
    lda #SIL_VALUE
:   sta SILENCE,x
    sta SILENCE+$100,x
    sta SILENCE+$200,x
    inx
    bne :-
    jsr wait_vb
    ; スプライト文字（バンク 0 の FONT_ADDR, 8x16 で 41 文字 = 82 タイル）を $1000 へ。
    ; タイル 254・255 は全部 $FF（スプライト 0 = 8x16 のタイル番号 $FF → 上半分が 254）
    lda #0
    jsr bank_a
    lda #$10
    sta PPUADDR
    lda #$00
    sta PPUADDR
    lda #<FONT_ADDR
    sta ptr
    lda #>FONT_ADDR
    sta ptr+1
    ldx #6
    ldy #0
:   lda (ptr),y
    sta PPUDATA
    iny
    bne :-
    inc ptr+1
    dex
    bne :-
    lda #$1F
    sta PPUADDR
    lda #$E0
    sta PPUADDR
    ldx #32
    lda #$FF
:   sta PPUDATA
    dex
    bne :-
    ; どの動画が入っているか
    ldx #0
:   txa
    asl a
    asl a
    asl a
    asl a
    tay
    lda CAT,y
    sta enabled,x
    inx
    cpx #NTITLES
    bne :-
    lda #2                  ; カーソルは BAD APPLE（原曲）/ まずオープニング（入っていなければメニュー）
    sta sel
    sta state
    ldx #0
    lda enabled
    bne :+
    jmp go_menu
:   jmp stream_start


; -----------------------------------------------------------------------------
; 再生を抜けたとき（後処理の 9 枠目から。レコードの準備などは済んでいる）
ev_button:
    lda state
    bne ev_play
    ; メニュー
    lda pad_new
    and #PAD_A
    beq @no_a
    ldx sel
    lda enabled,x
    beq @no_a
    lda #1
    sta state
    jmp stream_start
@no_a:
    lda pad_new
    and #PAD_SELECT
    beq @no_sel
    ldx mode
    inx
    cpx #3
    bcc :+
    ldx #0
:   stx mode
@no_sel:
    lda pad_new
    and #PAD_UP|PAD_DOWN
    beq @redraw
    ldx sel
@mv:
    lda pad_new
    and #PAD_UP
    beq :+
    dex
    dex
:   inx
    txa
    and #3
    tax
    lda enabled,x
    beq @mv
    stx sel
@redraw:
redraw:
    jsr build_oam           ; スプライトを書き直して、そのまま続ける（BGM は少し途切れる）
    jsr wait_vb
    jsr oam_dma
    jmp r_loop
ev_play:
    lda pad_new
    and #PAD_START
    beq :+
    jmp go_menu
:   lda pad_new
    and #PAD_SELECT
    beq :+
    jsr fps_toggle          ; FPS 表示の切り替え（「FPS」の文字はスプライトを書き直して出す）
    jmp redraw
:   jmp pause               ; B

; FPS 表示の ON/OFF。数字は半秒ごとなので、次のフレームで数字を書き直させる
fps_toggle:
    lda fps_on
    eor #1
    sta fps_on
    sta fps_show
    lda #29
    sta sec_tick
    rts

; 動画の終わり
ev_end:
    lda state
    bne :+
    ldx #MENU_ID            ; メニューの背景は繰り返し
    bne @rep
:   cmp #2
    bne :+
    jmp go_menu             ; 起動時のオープニングは終わったらメニューへ
:   lda mode
    cmp #MODE_STOP
    bne :+
    lda #1
    sta at_end
    jmp pause
:   ldx stream
    cmp #MODE_SEQ
    beq @nt
@rep:                       ; 繰り返し。つなぎ目なしの動画（目録 +7）は画面を消さずに先頭のレコードへ戻る
    lda ent+7
    beq @go
    lda #0
    sta frames
    sta frames+1
    jmp rec0
@nt:
    inx
    cpx #NTITLES
    bcc :+
    jmp go_menu             ; 全部見終わったらメニューへ
:   lda enabled,x
    beq @nt
@go:
    jmp stream_start

go_menu:
    lda #0
    sta state
    ldx #MENU_ID
    ; ↓ stream_start へ続く

; -----------------------------------------------------------------------------
; X = 動画の番号。画面を消して PPU をきれいにし、その動画の先頭から再生する
stream_start:
    stx stream
    lda #0
    sta PPUMASK
    sta at_end
    sta frames
    sta frames+1
    sta fps_count
    sta sec_tick
    lda #TILE_BLANK
    sta d1
    sta d2
    jsr wait_vb
    lda #$00                ; パターンテーブル $0000-$0FFF とネームテーブル $2000-$23FF を 0 に
    ldx #$10
    jsr fill_vram
    lda #$20
    ldx #$04
    jsr fill_vram
    lda #$0F                ; BG タイル 255 = plane1 だけ $FF（色番号 2）
    sta PPUADDR
    lda #$F8
    sta PPUADDR
    ldx #8
    lda #$FF
:   sta PPUDATA
    dex
    bne :-
    lda #$23                ; 右下のマス
    sta PPUADDR
    lda #$BF
    sta PPUADDR
    lda #RES_TILE
    sta PPUDATA
    ; 目録
    lda #0
    jsr bank_a
    lda stream
    asl a
    asl a
    asl a
    asl a
    tax
    ldy #0
:   lda CAT,x
    sta ent,y
    inx
    iny
    cpy #12
    bne :-
    ; パレット: BG は動画ごと（目録）、スプライトは共通
    lda #$3F
    sta PPUADDR
    lda #$00
    sta PPUADDR
    ldx #0
:   lda ent+8,x
    sta PPUDATA
    inx
    cpx #4
    bne :-
    lda #$0F
:   sta PPUDATA
    inx
    cpx #16
    bne :-
    ldx #0
:   lda spr_palette,x
    sta PPUDATA
    inx
    cpx #16
    bne :-
    ; 白黒の動画は、スナップショット 0 番（始めの中身。つなぎ目なしの動画では空っぽではない）を書く
    lda ent+3
    beq @no_snap
    lda ent+4
    sta ptr
    lda ent+5
    sta ptr+1
    jsr take8
    jsr snap_load
@no_snap:
    ; 再生を抜けて処理するボタンと、FPS を出すか（メニューでは出さない）
    ldx #PAD_UP|PAD_DOWN|PAD_A|PAD_SELECT
    lda #0
    ldy state
    beq :+
    ldx #PAD_B|PAD_START|PAD_SELECT
    lda fps_on
:   stx evmask
    sta fps_show
    jsr build_oam
    jsr oam_dma
    lda ent+2
    sta silent
rec0:                       ; 最初のバンク、最初のレコード（つなぎ目なしの繰り返しはここから）
    lda ent+1
    jsr bank_a
    lda #$80
    sta vptr+1
    lda #1
    sta vy
    ; ↓ resume へ続く

; -----------------------------------------------------------------------------
; vptr / vy のレコードから再生を始める（音声の位置はレコードの g - AUDIO_LEAD）
resume:
    lda $8000
    sta kcur
    lda #0
    sta vptr
    sta aptr
    sta bptr
    sta HDR_FLAGS
    sta cnt1
    sta pad_new
    lda #<ps1
    sta fin_vec
    lda #>ps1
    sta fin_vec+1
    ; g（レコードの +2,+3,+4）を読む
    lda vptr+1
    sta ptr+1
    lda vy
    clc
    adc #2
    sta ptr
    bcc :+
    inc ptr+1
:   ldy #0
    sec
    lda (ptr),y
    sbc #<AUDIO_LEAD
    sta ay
    iny
    lda (ptr),y
    sbc #>AUDIO_LEAD
    clc
    adc kcur
    sta aptr+1
    lda silent
    beq :+
    lda #>SILENCE
    sta aptr+1
:   lda ay                  ; 前のヒットの位置（最初のフレームの先読み用。だいたいでよい）
    sec
    sbc #54
    sta py
    lda aptr+1
    sbc #1
    sta ppage
    lda silent
    beq :+
    lda #>SILENCE
    sta ppage
:   jsr wait_vb
    lda #0
    sta PPUSCROLL
    sta PPUSCROLL
    lda #CTRL_TIMED
    sta PPUCTRL
    lda #MASK_ON
    sta PPUMASK
    jmp ps8                 ; 後処理の 8 枠目（次のレコードの準備）から入る

; -----------------------------------------------------------------------------
; 一時停止（音は止まる）。B 再開 / → コマ送り / ← コマ戻し / SELECT FPS / START メニュー
pause:
    lda #SIL_VALUE
    sta DMC_RAW
@loop:
    jsr wait_vb
    jsr readpad
    lda pad_new
    and #PAD_START
    beq :+
    jmp go_menu
:   lda pad_new
    and #PAD_B
    beq @no_b
    lda at_end
    beq :+
    ldx stream              ; 最後で止まっているときは最初から
    jmp stream_start
:   jmp resume
@no_b:
    lda pad_new
    and #PAD_SELECT
    beq @no_sel
    jsr fps_toggle          ; 「FPS」の文字はすぐ、数字は再開してから変わる
    jsr build_oam
    jsr oam_dma
@no_sel:
    lda at_end
    bne @loop
    lda pad_new
    and #PAD_RIGHT
    beq @no_r
@step:                      ; 1 コマ分（コマの終わりの印があるレコードまで）進める
    jsr ut_record
    lda HDR_FLAGS
    and #F_END
    beq :+
    lda #1
    sta at_end
    bne @loop
:   lda HDR_FLAGS
    and #F_FRAME_END
    beq @step
    jmp @loop
@no_r:
    lda pad_new
    and #PAD_LEFT
    beq @loop
    jsr step_back
    jmp @loop

readpad:
    lda pad
    sta pad_prev
    lda #1
    sta JOY1
    sta pad
    lsr a
    sta JOY1
:   lda JOY1
    lsr a
    rol pad
    bcc :-
    lda pad_prev
    eor #$FF
    and pad
    sta pad_new
    rts

; -----------------------------------------------------------------------------
; 普通の処理で、vptr のレコードを 1 つ読んで書く（画面を出したままなら VBlank を待つ）
;   cnt+1 = 1 なら画面を消したまま（待たない）
ut_record:
    ; BUF の上から下へ写す
    ldy vy
    lda (vptr),y
    tax
    lda len_tab,x
    sta cnt
    ldx #T_OFF
:   lda (vptr),y
    sta BUF,x
    dex
    iny
    bne :+
    inc vptr+1
:   dec cnt
    bne :--
    sty vy
    lda HDR_FLAGS
    and #F_FRAME_END
    beq :+
    inc frames
    bne :+
    inc frames+1
:   lda HDR_FLAGS
    and #F_NEXT_BANK
    beq :+
    lda bank
    clc
    adc #1
    jsr bank_a
    lda #$80
    sta vptr+1
    lda #1
    sta vy
:   ldx HDR_COUNT
    lda s_tab,x
    sta s_val
    ldx #NV + 1
    lda #SIL_VALUE
:   sta ABUF,x
    dex
    bpl :-
    lda cnt+1
    bne :+
    jsr wait_vb
:   lda #<upl_ret
    sta fin_vec
    lda #>upl_ret
    sta fin_vec+1
    tsx
    stx saved_s
    ldx s_val
    txs
    ldx #0
    rts                     ; 書き込み枠へ（仕上げの枠から upl_ret に来る）
; upl_ret（仕上げの枠から来て、ut_record を呼んだ所へ rts する）は bank_a の rts を使う

; -----------------------------------------------------------------------------
; ← コマ戻し: 少し前のスナップショットを PPU に書き、目的のコマまで（画面を消して）進める
step_back:
    lda ent+3               ; スナップショットがない動画（4 色）は何もしない
    bne :+
    rts
:   lda frames
    sec
    sbc #1
    sta target
    lda frames+1
    sbc #0
    sta target+1
    bcs :+
    rts                     ; 1 コマ目より前には戻らない
:   lda target
    ora target+1
    bne :+
    rts
:   ; スナップショットを選ぶ（コマ数が target 以下で一番新しいもの）
    lda #0
    jsr bank_a
    lda ent+4
    sta ptr
    lda ent+5
    sta ptr+1
    lda ent+3
    sta tmp
    ldx #0                  ; 選んだ件（ptr2 に 8 バイト）
@find:
    ldy #7
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
    jsr take8
    lda ptr
    clc
    adc #8
    sta ptr
    bcc :+
    inc ptr+1
:   dec tmp
    bne @find
@found:
    jsr snap_load
    jmp snap_cont

; snap（8 バイト）のスナップショットを PPU に書く（画面を消して）
snap_load:
    lda #0
    sta PPUMASK
    lda snap
    jsr bank_a
    lda snap+1
    sta ptr
    lda snap+2
    sta ptr+1
    bit PPUSTATUS
    lda #$20                ; ネームテーブル 960
    sta PPUADDR
    lda #$00
    sta PPUADDR
    lda #<960
    sta cnt
    lda #>960
    sta cnt+1
    jsr copy_cnt
    ldx #0                  ; 絵 256 枚 × 8 バイト（plane0）
@pat:
    txa
    lsr a
    lsr a
    lsr a
    lsr a
    sta PPUADDR
    txa
    asl a
    asl a
    asl a
    asl a
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
    lda #$3F                ; 白黒反転
    sta PPUADDR
    lda #$00
    sta PPUADDR
    ldy #0
    lda (ptr),y
    tax
    ldy #4                  ; 白黒反転: 0F 30 0F 30 / 30 0F 30 0F
:   lda inv_pal,x
    sta PPUDATA
    inx
    dey
    bne :-
    rts

snap_cont:                  ; 続きのレコードとコマ数
    lda snap+3
    jsr bank_a
    lda #0
    sta vptr
    lda snap+5
    sta vptr+1
    lda snap+4
    sta vy
    lda snap+6
    sta frames
    lda snap+7
    sta frames+1
    lda #1
    sta cnt+1               ; 画面を消したまま
@ff:
    lda frames
    cmp target
    bne :+
    lda frames+1
    cmp target+1
    beq @ff_done
:   jsr ut_record
    jmp @ff
@ff_done:
    lda #0
    sta cnt+1
    sta fps_count
    jsr wait_vb
    lda #0
    sta PPUSCROLL
    sta PPUSCROLL
    lda #MASK_ON
    sta PPUMASK
    rts

; ptr から cnt バイトを PPUDATA へ（ptr は進む）
copy_cnt:
    ldy #0
:   lda (ptr),y
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
    bne :---
    rts

; A = アドレス上位、X = ページ数。0 で埋める
fill_vram:
    sta PPUADDR
    lda #0
    sta PPUADDR
    ldy #0
:   sta PPUDATA
    iny
    bne :-
    dex
    bne :-
    rts

; -----------------------------------------------------------------------------
; スプライトを作り直す（OAM バッファ $0200）。0 番は同期用、62・63 番は FPS の数字
;   （バンク表の後ろ $FF00- の空きに置く）
.segment "CODE2"
wait_vb:
    bit PPUSTATUS
:   bit PPUSTATUS
    bpl :-
    rts

build_oam:
    ldx #0
    lda #$F0
:   sta OAM,x
    inx
    bne :-
    lda #SPR0_Y
    sta OAM+0
    lda #RES_TILE
    sta OAM+1
    lda #$20                ; 背景の後ろ
    sta OAM+2
    lda #SPR0_X
    sta OAM+3
    ldx #7
:   lda fps_oam,x
    sta OAM+FPS_OAM,x
    dex
    bpl :-
    lda #4
    sta oam_i
    lda state
    beq :+
    lda fps_show            ; 再生中: FPS を出すときだけ、左上に「FPS」（数字は仕上げの枠で毎フレーム書き換える）
    beq @r
    lda #<str_fps
    ldy #>str_fps
    jmp put_str_at
@r: rts
:   lda #<str_head
    ldy #>str_head
    jsr put_str_at
    lda #0
    sta tmp2
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
    ; ↓ put_str へ

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
    jmp put_str

.segment "CODE"
oam_dma:
    lda #0
    sta OAMADDR
    lda #>OAM
    sta OAMDMA
    rts

nmi:
irq:
    rti

; delay の NOP の列（最大 24 個 = 60 サイクル。delay で使う一番長い待ちに合わせる）
.repeat 24
    nop
.endrepeat
sled_end:
    rts

; =============================================================================
.segment "BSS"
snap:       .res 8

.segment "RODATA"           ; ページの頭から（pcm_video.cfg）。abs,X で読む表を先に置く
.macro TAB_ALIGN n
  .assert >* = >(* + (n) - 1), error, "表がページをまたぐ"
.endmacro
len_tab:                    ; レコードの長さ 5 + 12*count + 2
    TAB_ALIGN NV + 1
.repeat NV + 1, c
    .byte 7 + 12 * c
.endrepeat
s_tab:                      ; S = (レコードの本体の始まり - 1) の下位 = T_OFF - 7 - 12*count
    TAB_ALIGN NV + 1
.repeat NV + 1, c
    .byte <(T_OFF - 7 - 12 * c)
.endrepeat
guard_tab:                  ; ヒットを見始めるまでの枠数 = 277 - 13*count（次のレコード）。ここから今のフレームの count を引く
    TAB_ALIGN NV + 1        ;   描画中の枠 ≒ 298 - 今の count、写す 7+12*count・先読み count+2、ヒットの前に 12 枠ほど余裕
.repeat NV + 1, c
  .if 277 - 13 * c > 250
    .byte 250
  .else
    .byte 277 - 13 * c
  .endif
.endrepeat
tens_tab:                   ; FPS の数字 = 半秒に終わったコマ数 i × 2（i は 31 まで。32 = 表示しない）
    TAB_ALIGN 33
.repeat 32, i
  .if 2 * i < 10
    .byte TILE_BLANK
  .else
    .byte 2 * ((2 * i) / 10) + 1
  .endif
.endrepeat
    .byte TILE_BLANK
ones_tab:
    TAB_ALIGN 33
.repeat 32, i
    .byte 2 * ((2 * i) .mod 10) + 1
.endrepeat
    .byte TILE_BLANK
spr_palette:
    .byte $0F, $30, $10, $0F,  $0F, $28, $10, $0F,  $0F, $00, $10, $0F,  $0F, $21, $10, $0F
fps_oam:
    .byte 15, TILE_BLANK, 3, 48,  15, TILE_BLANK, 3, 56

; 位置つき文字列: x, y-1, 色, 文字..., STR_END
str_head:  .byte 16, 39, 3
           .byte "MOVIES", STR_END
str_mode:  .byte 16, 151, 3
           .byte "MODE", STR_END
; 1 行のスプライトは 8 個まで（ファミコンの制限）なので、題名は空白を除いて 8 文字まで
t0: .byte "OPENING", STR_END
t1: .byte "PUNINEKO", STR_END   ; メニューの背景の動画（データはメニューと同じ）
t2: .byte "BAD APPLE", STR_END  ; 原曲（ORIGEN）
t3 = t2                     ; 配布用サンプルには入っていない（メニューに出ない）
str_fps:   .byte 16, 15, 3
           .byte "FPS", STR_END
title_lo: .byte <t0, <t1, <t2, <t3
title_hi: .byte >t0, >t1, >t2, >t3
m0: .byte "REPEAT", STR_END
m1: .byte "STOP", STR_END
m2: .byte "SEQ", STR_END
mode_lo: .byte <m0, <m1, <m2
mode_hi: .byte >m0, >m1, >m2
inv_pal: .byte $0F, $30, $0F, $30, $0F   ; 白黒反転 0: 0F 30 0F 30 / 1: 30 0F 30 0F（色番号 2/3 = 0/1 と同じ色）

; ---- RODATA の残りに置く小さな処理（CODE に入りきらないため）
bank_a:                     ; A = バンク（バスの衝突を避けるため、同じ値が入った場所へ書く）
    sta bank
    tay
    sta banktable,y
upl_ret:                    ; ut_record の仕上げの枠からも来る（S は仕上げの枠で戻してある）
    rts
take8:                      ; ptr から 8 バイトを snap へ
    ldy #7
:   lda (ptr),y
    sta snap,y
    dey
    bpl :-
    rts

.segment "BANKTABLE"
banktable:
.repeat 256, i
    .byte i
.endrepeat

.segment "VECTORS"
    .word nmi, reset, irq
