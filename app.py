# --- app.py (VERSÃO COMPLETA COM TODOS OS COMANDOS IMPLEMENTADOS) ---

import os
import logging
import httpx
import json
import uuid
import base64
import io
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from quart import Quart, request, abort
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatInviteLink, User as TelegramUser, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, JobQueue, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.request import HTTPXRequest

import db_supabase as db
import scheduler
from admin_handlers import get_admin_conversation_handler
from utils import format_date_br, send_access_links

# --- CONFIGURAÇÃO DE LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- CARREGAMENTO E VALIDAÇÃO DE VARIÁVEIS ---
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")
WELCOME_ANIMATION_FILE_ID = os.getenv("WELCOME_ANIMATION_FILE_ID")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))
ADMIN_USER_IDS = os.getenv("ADMIN_USER_IDS")

if not all([
    TELEGRAM_BOT_TOKEN, TELEGRAM_SECRET_TOKEN, MERCADO_PAGO_ACCESS_TOKEN,
    WEBHOOK_BASE_URL, SUPABASE_URL, SUPABASE_KEY, PRODUCT_ID_LIFETIME, PRODUCT_ID_MONTHLY,
    ADMIN_USER_IDS, WELCOME_ANIMATION_FILE_ID
]):
    logger.critical("ERRO: Uma ou mais variáveis de ambiente essenciais não foram configuradas. Verifique o .env!")
    sys.exit(1)

NOTIFICATION_URL = f"{WEBHOOK_BASE_URL}/webhook/mercadopago"
TELEGRAM_WEBHOOK_URL = f"{WEBHOOK_BASE_URL}/webhook/telegram"
TIMEZONE_BR = timezone(timedelta(hours=-3))

# --- ESTADOS PARA CONVERSATION HANDLER DE CUPOM ---
GETTING_COUPON_CODE = range(1)

# --- INICIALIZAÇÃO DO BOT ---
request_config = {'connect_timeout': 10.0, 'read_timeout': 20.0}
httpx_request = HTTPXRequest(**request_config)
bot_app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(httpx_request).job_queue(JobQueue()).build()
app = Quart(__name__)

# --- HANDLERS DE COMANDOS DO USUÁRIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /start. Mostra as opções de pagamento."""
    tg_user = update.effective_user
    await db.get_or_create_user(tg_user)

    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
    product_lifetime = await db.get_product_by_id(PRODUCT_ID_LIFETIME)

    if not product_monthly or not product_lifetime:
        await update.message.reply_text("Desculpe, estamos com um problema em nossos sistemas. Tente novamente mais tarde.")
        logger.error("Não foi possível carregar os produtos do banco de dados.")
        return

    welcome_caption = (
        f"Olá, {tg_user.first_name}!\n\n"
        f"Bem-vindo(a) ao bot de acesso aos nossos *grupos exclusivos*.\n\n"
    )

    logger.info(f"Função /start: Tentando enviar animação com file_id: '{WELCOME_ANIMATION_FILE_ID}'")

    try:
        await update.message.reply_animation(
            animation=WELCOME_ANIMATION_FILE_ID,
            caption=welcome_caption,
            parse_mode=ParseMode.MARKDOWN
        )
    except BadRequest as e:
        logger.error(f"Falha ao enviar animação: {e}. Enviando mensagem de texto.")
        await update.message.reply_text(welcome_caption, parse_mode=ParseMode.MARKDOWN)

    follow_up_message = (
        f"Por uma assinatura *única* e *barata*, ou assinatura mensal você ganha acesso imediato. Pagamento fácil via PIX.\n\n"
        f"*Escolha seu plano de acesso:*"
    )

    keyboard = [
        [InlineKeyboardButton(f"✅ Assinatura Mensal (R$ {product_monthly['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_MONTHLY}')],
        [InlineKeyboardButton(f"💎 Acesso Vitalício (R$ {product_lifetime['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_LIFETIME}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        text=follow_up_message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /status. Mostra o status da assinatura."""
    tg_user = update.effective_user
    subscription = await db.get_user_active_subscription(tg_user.id)

    if subscription and subscription.get('status') == 'active':
        product_name = subscription.get('product', {}).get('name', 'N/A')
        start_date_br = format_date_br(subscription.get('start_date'))

        if subscription.get('end_date'):
            end_date_br = format_date_br(subscription.get('end_date'))

            # Calcula dias restantes
            end_date = datetime.fromisoformat(subscription.get('end_date')).astimezone(TIMEZONE_BR)
            days_left = (end_date - datetime.now(TIMEZONE_BR)).days

            message = (
                "📄 *Status da sua Assinatura*\n\n"
                f"📦 *Plano:* {product_name}\n"
                f"✅ *Status:* Ativa\n"
                f"📅 *Início:* {start_date_br}\n"
                f"📆 *Vencimento:* {end_date_br}\n"
                f"⏳ *Dias restantes:* {days_left} dias\n\n"
            )

            if days_left <= 3:
                message += "⚠️ *Sua assinatura está perto de vencer!*\nPara renovar, use o comando /renovar."
            else:
                message += "Você tem acesso a todos os nossos grupos. Para renovar, use /renovar."
        else:
            message = (
                "📄 *Status do seu Acesso*\n\n"
                f"📦 *Plano:* {product_name}\n"
                f"✅ *Status:* Ativo\n"
                f"📅 *Data de Início:* {start_date_br}\n\n"
                "💎 Seu acesso é vitalício e não expira!"
            )
    else:
        message = "❌ Você não possui uma assinatura ativa no momento.\n\nUse o comando /start para ver as opções de planos disponíveis."

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def renew_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /renovar."""
    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
    if not product_monthly:
        await update.message.reply_text("Erro ao buscar informações de renovação. Tente mais tarde.")
        return

    message = (
        f"🔄 *Renovação de Assinatura*\n\n"
        f"Para renovar sua assinatura mensal por mais 30 dias:\n"
        f"💰 Valor: R$ {product_monthly['price']:.2f}\n\n"
        f"Clique no botão abaixo para gerar o pagamento PIX."
    )

    keyboard = [[InlineKeyboardButton(f"💳 Pagar Renovação (R$ {product_monthly['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_MONTHLY}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /suporte."""
    message = (
        "🆘 *Central de Suporte*\n\n"
        "Selecione uma opção de suporte:\n\n"
        "📗 *Reenviar Links:* Se você já pagou e perdeu os links de acesso.\n"
        "💰 *Problema no Pagamento:* Se precisa de ajuda com um pagamento."
    )
    keyboard = [
        [InlineKeyboardButton("📗 Reenviar Links de Acesso", callback_data='support_resend_links')],
        [InlineKeyboardButton("💰 Ajuda com Pagamento", callback_data='support_payment_help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


async def meuslinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /meuslinks - Reenvia os links de acesso."""
    tg_user = update.effective_user
    subscription = await db.get_user_active_subscription(tg_user.id)

    if subscription and subscription.get('status') == 'active':
        await update.message.reply_text("📬 Verificando seus acessos e gerando novos links...")
        await send_access_links(context.bot, tg_user.id, subscription['mp_payment_id'], is_support_request=True)
    else:
        await update.message.reply_text(
            "❌ Você não possui uma assinatura ativa no momento.\n\n"
            "Use /start para ver os planos disponíveis ou /suporte se você acredita que isso é um erro."
        )


# --- NOVO: COMANDO /CUPOM ---
async def cupom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o processo de aplicação de cupom."""
    message = (
        "🎟️ *Aplicar Cupom de Desconto*\n\n"
        "Digite o código do cupom que você recebeu:\n\n"
        "Exemplo: `PROMO20`, `DESCONTO10`\n\n"
        "Use /cancel para cancelar."
    )
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    return GETTING_COUPON_CODE


async def cupom_apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Aplica o cupom e mostra o desconto."""
    coupon_code = update.message.text.strip().upper()

    # Busca o cupom no banco
    coupon = await db.get_coupon_by_code(coupon_code)

    if not coupon:
        await update.message.reply_text(
            "❌ Cupom inválido ou expirado.\n\n"
            "Verifique o código e tente novamente ou use /cancel para sair."
        )
        return GETTING_COUPON_CODE

    # Verifica validade
    now = datetime.now(TIMEZONE_BR)

    if coupon.get('valid_from'):
        valid_from = datetime.fromisoformat(coupon['valid_from']).astimezone(TIMEZONE_BR)
        if now < valid_from:
            await update.message.reply_text(
                f"⏰ Este cupom só será válido a partir de {format_date_br(coupon['valid_from'])}.\n\n"
                "Tente novamente mais tarde!"
            )
            return ConversationHandler.END

    if coupon.get('valid_until'):
        valid_until = datetime.fromisoformat(coupon['valid_until']).astimezone(TIMEZONE_BR)
        if now > valid_until:
            await update.message.reply_text(
                f"❌ Este cupom expirou em {format_date_br(coupon['valid_until'])}.\n\n"
                "Infelizmente não pode mais ser usado."
            )
            return ConversationHandler.END

    # Verifica limite de uso
    if coupon.get('usage_limit'):
        if coupon.get('usage_count', 0) >= coupon['usage_limit']:
            await update.message.reply_text(
                "❌ Este cupom atingiu o limite de usos.\n\n"
                "Infelizmente não pode mais ser usado."
            )
            return ConversationHandler.END

    # Salva o cupom no contexto do usuário
    context.user_data['active_coupon'] = coupon

    # Calcula e mostra o desconto
    discount_type = coupon['discount_type']
    discount_value = coupon['discount_value']

    if discount_type == 'percentage':
        discount_text = f"{discount_value}% de desconto"
    else:
        discount_text = f"R$ {discount_value:.2f} de desconto"

    # Busca os produtos para mostrar preços com desconto
    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
    product_lifetime = await db.get_product_by_id(PRODUCT_ID_LIFETIME)

    # Calcula preços com desconto
    if discount_type == 'percentage':
        monthly_final = product_monthly['price'] * (1 - discount_value / 100)
        lifetime_final = product_lifetime['price'] * (1 - discount_value / 100)
    else:
        monthly_final = max(0, product_monthly['price'] - discount_value)
        lifetime_final = max(0, product_lifetime['price'] - discount_value)

    message = (
        f"✅ *Cupom aplicado com sucesso!*\n\n"
        f"🎟️ Código: `{coupon_code}`\n"
        f"💰 Desconto: {discount_text}\n\n"
        f"*Preços com desconto:*\n"
        f"📅 Mensal: ~~R$ {product_monthly['price']:.2f}~~ → *R$ {monthly_final:.2f}*\n"
        f"💎 Vitalício: ~~R$ {product_lifetime['price']:.2f}~~ → *R$ {lifetime_final:.2f}*\n\n"
        f"Use /start para escolher seu plano!\n\n"
        f"⚠️ O desconto será aplicado automaticamente no pagamento."
    )

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

    # Registra no log
    await db.create_log(
        'coupon_applied',
        f"Usuário {update.effective_user.id} aplicou cupom {coupon_code}"
    )

    return ConversationHandler.END


async def cupom_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a aplicação do cupom."""
    await update.message.reply_text("❌ Aplicação de cupom cancelada.")
    context.user_data.pop('active_coupon', None)
    return ConversationHandler.END


# --- NOVO: COMANDO /INDICAR (Para implementação futura de sistema de referência) ---
async def indicar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /indicar - Gera código de indicação pessoal."""
    tg_user = update.effective_user

    # Busca ou cria código de referência do usuário
    user_data = await db.get_or_create_user(tg_user)
    referral_code = user_data.get('referral_code')

    if not referral_code:
        # Gera um código único
        referral_code = f"REF{tg_user.id}"
        await db.update_user_referral_code(tg_user.id, referral_code)

    message = (
        f"🎁 *Seu Código de Indicação*\n\n"
        f"Compartilhe seu código com amigos:\n"
        f"🔑 `{referral_code}`\n\n"
        f"*Como funciona?*\n"
        f"1️⃣ Seu amigo usa o código ao se cadastrar\n"
        f"2️⃣ Quando ele fizer a primeira compra\n"
        f"3️⃣ Você ganha 7 dias grátis!\n\n"
        f"*Compartilhe:*\n"
        f"Hey! Use o código `{referral_code}` e ganhe desconto na sua primeira compra!\n\n"
        f"_Sistema de indicação em breve!_"
    )

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


# --- HANDLERS DE DEBUG (ADMIN) ---
async def test_animation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Um comando de admin para testar o envio da animação de boas-vindas."""
    ADMIN_IDS_STR = os.getenv("ADMIN_USER_IDS", "")
    ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',')] if ADMIN_IDS_STR else []

    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Você não tem permissão para usar este comando.")
        return

    animation_id = os.getenv("WELCOME_ANIMATION_FILE_ID")
    logger.info(f"[DEBUG] Comando /testanimation acionado. Tentando usar o file_id: '{animation_id}'")

    if not animation_id:
        await update.message.reply_text("A variável de ambiente WELCOME_ANIMATION_FILE_ID não está configurada.")
        return

    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped_animation_id = animation_id
    for char in escape_chars:
        escaped_animation_id = escaped_animation_id.replace(char, f'\\{char}')

    await update.message.reply_text(
        f"Tentando enviar a animação com o seguinte file\\_id:\n\n`{escaped_animation_id}`",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    try:
        await context.bot.send_animation(
            chat_id=user_id,
            animation=animation_id,
            caption="✅ Se você vê esta animação, o file_id está correto!"
        )
        logger.info(f"[DEBUG] Animação de teste enviada com sucesso para o admin {user_id}.")
    except BadRequest as e:
        error_message = f"❌ *FALHA* ao enviar a animação\\.\n\n" \
                        f"**Erro do Telegram:** `{e.message}`\n\n" \
                        f"O file\\_id usado foi:\n`{escaped_animation_id}`\n\n" \
                        "Isso confirma que o `file_id` é inválido para este bot\\. Tente a alternativa da URL pública\\."
        await update.message.reply_text(error_message, parse_mode=ParseMode.MARKDOWN_V2)
        logger.error(f"[DEBUG] Falha no /testanimation ao enviar a animação. Erro: {e.message}", exc_info=True)
    except Exception as e:
        await update.message.reply_text(f"Ocorreu um erro inesperado: {e}")
        logger.error(f"[DEBUG] Erro inesperado no /testanimation: {e}", exc_info=True)


async def get_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde a uma mensagem com o file_id do anexo."""
    try:
        replied_message = update.message.reply_to_message
        if not replied_message:
            await update.message.reply_text(
                "ℹ️ Por favor, use este comando *respondendo* a uma mensagem que contenha uma foto, vídeo, GIF ou documento.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        file_id = None
        file_type = ""

        if replied_message.animation:
            file_id = replied_message.animation.file_id
            file_type = "Animação (GIF)"
        elif replied_message.photo:
            file_id = replied_message.photo[-1].file_id
            file_type = "Foto"
        elif replied_message.video:
            file_id = replied_message.video.file_id
            file_type = "Vídeo"
        elif replied_message.document:
            file_id = replied_message.document.file_id
            file_type = "Documento"
        elif replied_message.sticker:
            file_id = replied_message.sticker.file_id
            file_type = "Sticker"

        if file_id:
            message = (
                f"ℹ️ *Detalhes do Arquivo*\n\n"
                f"📁 *Tipo:* {file_type}\n"
                f"🆔 *File ID:*\n"
                f"```{file_id}```\n\n"
                f"👆 Toque no ID acima para copiar."
            )
            await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "❌ A mensagem respondida não parece conter uma mídia com um file_id que eu possa extrair."
            )

    except Exception as e:
        logger.error(f"Erro no comando /getid: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ocorreu um erro ao processar o comando: {e}")


# --- HANDLER DE BOTÕES (CALLBACKQUERY) ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa todos os cliques em botões."""
    query = update.callback_query
    await query.answer()
    tg_user = query.from_user
    chat_id = query.message.chat_id
    data = query.data

    # Fluxo de Pagamento
    if data.startswith('pay_'):
        product_id = int(data.split('_')[1])
        product = await db.get_product_by_id(product_id)
        if not product:
            await query.edit_message_text(text="Desculpe, este produto não está mais disponível.")
            return

        # Verifica se há cupom ativo
        active_coupon = context.user_data.get('active_coupon')
        final_price = product['price']

        if active_coupon:
            discount_type = active_coupon['discount_type']
            discount_value = active_coupon['discount_value']

            if discount_type == 'percentage':
                final_price = product['price'] * (1 - discount_value / 100)
            else:
                final_price = max(0, product['price'] - discount_value)

            await query.edit_message_text(
                text=f"✅ Cupom aplicado! Desconto ativo.\n\n"
                f"Gerando sua cobrança PIX para o plano '{product['name']}'...\n"
                f"💰 Valor original: R$ {product['price']:.2f}\n"
                f"🎟️ Valor com desconto: R$ {final_price:.2f}"
            )
        else:
            await query.edit_message_text(text=f"Gerando sua cobrança PIX para o plano '{product['name']}', aguarde...")

        payment_data = await create_pix_payment(tg_user, product, final_price, active_coupon)

        if payment_data:
            qr_code_image = base64.b64decode(payment_data['qr_code_base64'])
            image_stream = io.BytesIO(qr_code_image)
            await context.bot.send_photo(chat_id=chat_id, photo=image_stream, caption="Use o QR Code acima ou o código abaixo para pagar.")
            await context.bot.send_message(chat_id=chat_id, text=f"PIX Copia e Cola:\n\n`{payment_data['pix_copy_paste']}`", parse_mode=ParseMode.MARKDOWN_V2)
            await context.bot.send_message(chat_id=chat_id, text="✅ Assim que o pagamento for confirmado, você receberá o(s) link(s) de acesso automaticamente!")

            # Limpa o cupom do contexto após uso
            context.user_data.pop('active_coupon', None)
        else:
            await query.edit_message_text(text="Desculpe, ocorreu um erro ao gerar sua cobrança. Tente novamente mais tarde ou use /suporte.")

    # Fluxo de Suporte
    elif data == 'support_resend_links':
        await query.edit_message_text("Verificando sua assinatura, um momento...")
        subscription = await db.get_user_active_subscription(tg_user.id)
        if subscription and subscription.get('status') == 'active':
            await query.edit_message_text("Encontramos sua assinatura ativa! Verificando seus acessos e reenviando links se necessário...")
            await send_access_links(context.bot, tg_user.id, subscription['mp_payment_id'], is_support_request=True)
        else:
            await query.edit_message_text("Não encontrei uma assinatura ativa para você. Se você já pagou, use a opção 'Ajuda com Pagamento' ou aguarde alguns minutos pela confirmação.")

    elif data == 'support_payment_help':
        chave_pix = "234caf84-775c-4649-aaf1-ab7d928ef315"
        usuario_suporte = "@sirigueijo"
        usuario_suporte_escapado = usuario_suporte.replace("_", "\\_")

        texto = (
            "💡 *Ajuda com Pagamento*\n\n"
            "Se o pagamento automático falhou, você pode tentar pagar manualmente para a chave PIX:\n\n"
            f"`{chave_pix}`\n\n"
            f"*IMPORTANTE:* Após o pagamento manual, envie o comprovante para {usuario_suporte_escapado} para liberação\\."
        )

        await query.edit_message_text(
            text=texto,
            parse_mode=ParseMode.MARKDOWN_V2
        )


# --- LÓGICA DE PAGAMENTO E ACESSO ---

async def create_pix_payment(tg_user: TelegramUser, product: dict, final_price: float, coupon: dict = None) -> dict | None:
    """Cria uma cobrança PIX no Mercado Pago e uma assinatura pendente no DB."""
    url = "https://api.mercadopago.com/v1/payments"
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4())
    }

    external_ref = f"user:{tg_user.id};product:{product['id']}"
    if coupon:
        external_ref += f";coupon:{coupon['id']}"

    payload = {
        "transaction_amount": float(final_price),
        "description": f"Acesso '{product['name']}' para {tg_user.first_name}",
        "payment_method_id": "pix",
        "payer": { "email": f"user_{tg_user.id}@telegram.bot" },
        "notification_url": NOTIFICATION_URL,
        "external_reference": external_ref
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
        data = response.json()
        mp_payment_id = str(data.get('id'))

        db_user = await db.get_or_create_user(tg_user)
        if db_user and db_user.get('id'):
            await db.create_pending_subscription(
                db_user['id'],
                product['id'],
                mp_payment_id,
                original_price=product['price'],
                final_price=final_price,
                coupon_id=coupon['id'] if coupon else None
            )
        else:
            logger.error(f"Não foi possível obter/criar o usuário do DB para {tg_user.id}.")
            return None

        return {
            'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'],
            'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code']
        }
    except httpx.HTTPError as e:
        logger.error(f"Erro HTTP ao criar pagamento no Mercado Pago: {e}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao criar pagamento ou transação: {e}", exc_info=True)
        return None


async def process_approved_payment(payment_id: str):
    """Processa um pagamento aprovado, ativa a assinatura e agenda o envio dos links."""
    logger.info(f"[{payment_id}] Iniciando processamento de pagamento aprovado.")

    activated_subscription = await db.activate_subscription(payment_id)

    if activated_subscription:
        telegram_user_id = activated_subscription.get('user', {}).get('telegram_user_id')

        if telegram_user_id:
            logger.info(f"[{payment_id}] Assinatura ativada. Agendando envio de links para o usuário {telegram_user_id}.")
            asyncio.create_task(send_access_links(bot_app.bot, telegram_user_id, payment_id))
        else:
            logger.error(f"[{payment_id}] CRÍTICO: Assinatura ativada, mas não foi possível encontrar o telegram_user_id associado.")
    else:
        logger.warning(f"[{payment_id}] A ativação da assinatura falhou ou já estava ativa.")


# --- WEBHOOKS E CICLO DE VIDA ---

# 1. ConversationHandler do Cupom
cupom_handler = ConversationHandler(
    entry_points=[CommandHandler("cupom", cupom_start)],
    states={
        GETTING_COUPON_CODE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, cupom_apply)
        ],
    },
    fallbacks=[CommandHandler("cancel", cupom_cancel)],
    per_user=True,
)

# 2. Adicione os handlers na ordem correta
bot_app.add_handler(get_admin_conversation_handler())
bot_app.add_handler(cupom_handler)

# 3. Comandos regulares
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("status", status_command))
bot_app.add_handler(CommandHandler("renovar", renew_command))
bot_app.add_handler(CommandHandler("suporte", support_command))
bot_app.add_handler(CommandHandler("meuslinks", meuslinks_command))
bot_app.add_handler(CommandHandler("indicar", indicar_command))
bot_app.add_handler(CommandHandler("testanimation", test_animation_command))
bot_app.add_handler(CommandHandler("getid", get_id_command))

# 4. CallbackQueryHandler geral por último
bot_app.add_handler(CallbackQueryHandler(button_handler))

# --- ROTA PARA EXECUTAR O SCHEDULER EXTERNAMENTE ---
SCHEDULER_SECRET_TOKEN = os.getenv("SCHEDULER_SECRET_TOKEN")

@app.route("/webhook/run-scheduler", methods=['POST'])
async def run_scheduler_webhook():
    auth_token = request.headers.get("Authorization")
    if not SCHEDULER_SECRET_TOKEN or auth_token != f"Bearer {SCHEDULER_SECRET_TOKEN}":
        logger.warning("Tentativa de acesso não autorizado ao webhook do scheduler.")
        abort(403)

    logger.info("Webhook do scheduler acionado. Executando tarefas agendadas...")

    async def run_tasks():
        logger.info("--- Iniciando verificação do scheduler ---")
        await scheduler.find_and_process_expiring_subscriptions(db.supabase, bot_app.bot)
        await scheduler.find_and_process_expired_subscriptions(db.supabase, bot_app.bot)
        logger.info("--- Verificação do scheduler concluída ---")

    asyncio.create_task(run_tasks())
    return "Scheduler tasks triggered.", 200


@app.before_serving
async def startup():
    await bot_app.initialize()
    await bot_app.start()

    # Define a lista de comandos que aparecerão no menu
    commands = [
        BotCommand("start", "▶️ Inicia o bot e mostra os planos"),
        BotCommand("status", "📄 Verifica o status da sua assinatura"),
        BotCommand("renovar", "🔄 Renovar assinatura mensal"),
        BotCommand("suporte", "🆘 Ajuda com pagamentos ou links de acesso"),
        BotCommand("meuslinks", "📬 Reenviar links de acesso aos grupos"),
        BotCommand("cupom", "🎟️ Aplicar cupom de desconto"),
        BotCommand("indicar", "🎁 Gerar código de indicação"),
    ]
    await bot_app.bot.set_my_commands(commands)
    logger.info("✅ Comandos do menu registrados com sucesso.")

    await bot_app.bot.set_webhook(url=TELEGRAM_WEBHOOK_URL, secret_token=TELEGRAM_SECRET_TOKEN)
    logger.info("✅ Bot inicializado e webhook registrado com sucesso.")

@app.after_serving
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Bot desligado.")

@app.route("/")
async def health_check():
    return "Bot is alive and running!", 200

@app.route("/webhook/telegram", methods=['POST'])
async def telegram_webhook():
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_token != TELEGRAM_SECRET_TOKEN:
        abort(403)
    try:
        update_data = await request.get_json()
        update = Update.de_json(update_data, bot_app.bot)
        await bot_app.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Erro no webhook do Telegram: {e}", exc_info=True)
        return "Error", 500

@app.route("/webhook/mercadopago", methods=['POST'])
async def mercadopago_webhook():
    data = await request.get_json()
    logger.info(f"Webhook do MP recebido: {json.dumps(data)}")

    if data and data.get("action") == "payment.updated":
        payment_id = data.get("data", {}).get("id")
        if payment_id:
            try:
                async with httpx.AsyncClient() as client:
                    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
                    response = await client.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers)
                    payment_info = response.json()

                if response.status_code == 200 and payment_info.get("status") == "approved":
                    logger.info(f"Pagamento {payment_id} confirmado como 'approved'. Agendando processamento.")
                    asyncio.create_task(process_approved_payment(str(payment_id)))
                else:
                    logger.info(f"Notificação para pagamento {payment_id} recebida, mas status não é 'approved' (Status: {payment_info.get('status')}). Ignorando.")

            except Exception as e:
                logger.error(f"Erro ao verificar status do pagamento {payment_id} na API do MP: {e}")

    return "OK", 200

# --- FIM DO ARQUIVO ---
