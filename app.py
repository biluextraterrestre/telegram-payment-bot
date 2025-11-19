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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatInviteLink, User as TelegramUser, BotCommand, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, JobQueue, ConversationHandler, ChatMemberHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.request import HTTPXRequest

import db_supabase as db
import scheduler
from admin_handlers import get_admin_conversation_handler, ADMIN_IDS, states_list
from utils import format_date_br, send_access_links, alert_admins
from menu_handlers import register_menu_handlers

# --- CONFIGURAÇÃO DE LOGGING ------
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

# --- INICIALIZAÇÃO DO BOT ---
request_config = {'connect_timeout': 10.0, 'read_timeout': 20.0}
httpx_request = HTTPXRequest(**request_config)
bot_app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(httpx_request).job_queue(JobQueue()).build()
app = Quart(__name__)

# --- HANDLERS DE COMANDOS DO USUÁRIO ---

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
                message += "Você tem acesso a todos os nossos canais. Para renovar, use /renovar."
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
        await update.message.reply_text("📬 Gerando seus novos links, um momento...")
        # Apenas chama a função. A confirmação e o botão virão de lá.
        await send_access_links(context.bot, tg_user.id, subscription['mp_payment_id'], access_type='support')
    else:
        # ... (lógica de erro não muda)
        await update.message.reply_text(
            "❌ Você não possui uma assinatura ativa no momento."
        )


async def get_state_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando de debug para verificar o estado atual da ConversationHandler."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Comando restrito.")
        return

    state = None
    conv_key_found = None

    for key in context.user_data.keys():
        if isinstance(key, tuple) and key[0] == 'admin-conversation':
            conv_key_found = key
            break

    if conv_key_found and context.user_data.get(conv_key_found):
        state_tuple = context.user_data[conv_key_found]
        state = state_tuple[0]

    # --- INÍCIO DA CORREÇÃO DE FORMATAÇÃO ---

    # Função auxiliar para escapar caracteres do MarkdownV2
    def escape_markdown(text: str) -> str:
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))

    raw_data_str = escape_markdown(str(context.user_data))

    if state is not None:
        state_name = states_list[state] if isinstance(state, int) and state < len(states_list) else "Desconhecido"
        message = (
            f"✅ Conversa ativa encontrada\\!\n"
            f"ℹ️ Estado atual: *{state} ({escape_markdown(state_name)})*\n\n"
            f"🔍 Raw user\\_data:\n`{raw_data_str}`"
        )
    else:
        message = (
            f"ℹ️ Nenhuma conversa ativa encontrada para você\\.\n\n"
            f"🔍 Raw user\\_data:\n`{raw_data_str}`"
        )

    # Usar MARKDOWN_V2 que é mais consistente com o escape
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)
    # --- FIM DA CORREÇÃO DE FORMATAÇÃO ---

# --- NOVO: COMANDO /CUPOM ---
async def handle_coupon_code_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Verifica se o bot está aguardando um cupom e, se estiver, processa a mensagem.
    """
    # Se a flag 'awaiting_coupon' não estiver no user_data, ignora a mensagem.
    if not context.user_data.get('awaiting_coupon'):
        return

    # Remove a flag para não processar a próxima mensagem como um cupom.
    context.user_data.pop('awaiting_coupon', None)

    # Reutiliza a lógica já existente do comando /cupom para aplicar o código.
    await cupom_apply(update, context)

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
    """
    Verifica se o código é um cupom de desconto ou um código de indicação e o processa.
    """
    code = update.message.text.strip().upper()
    user = update.effective_user

    # Limpa dados de sessões anteriores para evitar conflitos
    context.user_data.pop('active_coupon', None)
    context.user_data.pop('referral_info', None)

    # --- LÓGICA 1: TENTAR APLICAR COMO CUPOM DE DESCONTO ---
    coupon = await db.get_coupon_by_code(code)
    if coupon:
        # Validações do cupom (data de validade, limite de uso, etc.)
        now = datetime.now(TIMEZONE_BR)
        if coupon.get('valid_until') and now > datetime.fromisoformat(coupon['valid_until']).astimezone(TIMEZONE_BR):
            await update.message.reply_text(f"❌ Este cupom expirou em {format_date_br(coupon['valid_until'])}.")
            return ConversationHandler.END

        if coupon.get('usage_limit') and coupon.get('usage_count', 0) >= coupon['usage_limit']:
            await update.message.reply_text("❌ Este cupom atingiu o limite máximo de usos.")
            return ConversationHandler.END

        # Cupom válido, armazena no contexto e mostra os descontos
        context.user_data['active_coupon'] = coupon

        discount_type = coupon['discount_type']
        discount_value = coupon['discount_value']
        discount_text = f"{discount_value}% de desconto" if discount_type == 'percentage' else f"R$ {discount_value:.2f} de desconto"

        product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
        product_lifetime = await db.get_product_by_id(PRODUCT_ID_LIFETIME)

        if discount_type == 'percentage':
            monthly_final = product_monthly['price'] * (1 - discount_value / 100)
            lifetime_final = product_lifetime['price'] * (1 - discount_value / 100)
        else:
            monthly_final = max(0, product_monthly['price'] - discount_value)
            lifetime_final = max(0, product_lifetime['price'] - discount_value)

        message = (
            f"✅ *Cupom aplicado com sucesso!*\n\n"
            f"🎟️ Código: `{code}`\n"
            f"💰 Desconto: {discount_text}\n\n"
            f"*Preços com desconto:*\n"
            f"📅 Mensal: ~~R$ {product_monthly['price']:.2f}~~ → *R$ {monthly_final:.2f}*\n"
            f"💎 Vitalício: ~~R$ {product_lifetime['price']:.2f}~~ → *R$ {lifetime_final:.2f}*\n\n"
            f"Use /start para escolher seu plano e o desconto será aplicado no pagamento!"
        )
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        await db.create_log('coupon_applied', f"Usuário {user.id} aplicou cupom {code}")
        return ConversationHandler.END

    # --- LÓGICA 2: SE NÃO FOR CUPOM, TENTAR COMO CÓDIGO DE INDICAÇÃO ---
    referrer = await db.find_user_by_referral_code(code)
    if referrer:
        # Validação para impedir que o usuário use o próprio código
        if referrer['telegram_user_id'] == user.id:
            await update.message.reply_text("❌ Você não pode usar seu próprio código de indicação. Tente outro código ou use /cancel.")
            return GETTING_COUPON_CODE

        # Código de indicação válido, armazena os dados para uso após o pagamento
        context.user_data['referral_info'] = {
            "referrer_db_id": referrer['id'],
            "code": code
        }
        message = (
            f"✅ *Código de indicação aplicado!*\n\n"
            f"Você foi indicado(a) pelo usuário do código `{code}`.\n\n"
            f"Quando você concluir sua primeira compra, ele(a) receberá uma recompensa. "
            f"Use /start para ver os planos!"
        )
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        await db.create_log('referral_code_applied', f"Usuário {user.id} aplicou código de indicação {code} do usuário {referrer['id']}")
        return ConversationHandler.END

    # --- LÓGICA 3: SE NÃO FOR NENHUM DOS DOIS ---
    await update.message.reply_text(
        "❌ Código inválido. Não encontramos um cupom de desconto ou código de indicação com este nome.\n\n"
        "Verifique o código e tente novamente, ou use /cancel."
    )
    return GETTING_COUPON_CODE


async def cupom_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a aplicação do cupom."""
    await update.message.reply_text("❌ Aplicação de cupom cancelada.")
    context.user_data.pop('active_coupon', None)
    return ConversationHandler.END


# --- NOVO: COMANDO /INDICAR (Para implementação futura de sistema de referência) ---
async def indicar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /indicar - Gera e/ou mostra o código de indicação pessoal."""
    tg_user = update.effective_user

    # Gera o código de forma determinística. Ele sempre será o mesmo para o mesmo usuário.
    referral_code = f"REF{tg_user.id}"

    # Garante que o usuário e seu código de indicação existam no banco de dados.
    # A função `ensure_referral_code_exists` tentará inserir o código.
    # Se já existir, o banco de dados (graças à constraint UNIQUE) simplesmente ignora,
    # garantindo que o código seja salvo apenas na primeira vez.
    await db.ensure_referral_code_exists(tg_user.id, referral_code)

    # Mensagem para o usuário com o texto para compartilhar
    share_text = (
        f"Ei! Estou usando um bot incrível e lembrei de você. "
        f"Use meu código **{referral_code}** no comando /cupom antes de comprar para me ajudar a ganhar uma recompensa!"
    )

    message = (
        f"🎁 *Seu Programa de Indicação*\n\n"
        f"Compartilhe seu código pessoal com amigos e ganhe *7 dias de acesso grátis* para cada amigo que se tornar assinante!\n\n"
        f"Seu código é:\n"
        f"🔑 `{referral_code}`\n\n"
        f"👇 *Copie a mensagem abaixo e envie para seus amigos:*\n\n"
        f"`{share_text}`"
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


# --- LÓGICA DE PAGAMENTO E ACESSO ---

async def create_pix_payment(tg_user: TelegramUser, product: dict, final_price: float, coupon: dict = None, referral_info: dict = None) -> dict | None:
    """Cria uma cobrança PIX no Mercado Pago e uma assinatura pendente no DB."""
    url = "https://api.mercadopago.com/v1/payments"
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4())
    }

    # --- MODIFICAÇÃO IMPORTANTE AQUI ---
    db_user = await db.get_or_create_user(tg_user)
    if not db_user:
        logger.error(f"Não foi possível obter/criar o usuário do DB para {tg_user.id}.")
        return None

    external_ref = f"user_db_id:{db_user['id']};product_id:{product['id']}"
    if coupon:
        external_ref += f";coupon_id:{coupon['id']}"
    if referral_info:
        external_ref += f";referrer_db_id:{referral_info['referrer_db_id']};ref_code:{referral_info['code']}"
    # --- FIM DA MODIFICAÇÃO ---

    payload = {
        "transaction_amount": float(round(final_price, 2)),
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

        await db.create_pending_subscription(
            db_user_id=db_user['id'],
            product_id=product['id'],
            mp_payment_id=mp_payment_id,
            original_price=product['price'],
            final_price=final_price,
            coupon_id=coupon['id'] if coupon else None,
            external_reference=external_ref # Salva a referência no DB
        )
        return {
            'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'],
            'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code']
        }
    except httpx.HTTPError as e:
        logger.error(f"Erro HTTP ao criar pagamento no Mercado Pago: {e} - Resposta: {e.response.text}")

        error_message = (
            f"Falha CRÍTICA ao criar pagamento no Mercado Pago para o usuário {tg_user.id} (@{tg_user.username}).\n\n"
            f"**Erro:** `{e}`\n"
            f"**Resposta da API:** ```{e.response.text[:500]}```"
        )
        await alert_admins(bot_app.bot, error_message)

        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao criar pagamento ou transação: {e}", exc_info=True)

        error_message = (
            f"Erro INESPERADO ao criar pagamento para o usuário {tg_user.id} (@{tg_user.username}).\n\n"
            f"**Tipo de Erro:** `{type(e).__name__}`\n"
            f"**Mensagem:** `{str(e)[:500]}`"
        )
        await alert_admins(bot_app.bot, error_message)

        return None


async def process_approved_payment(payment_id: str):
    """Processa pagamento, ativa assinatura e CONCEDE RECOMPENSA DE INDICAÇÃO."""
    logger.info(f"[{payment_id}] Iniciando processamento de pagamento aprovado.")

    activated_subscription = await db.activate_subscription(payment_id)

    if not activated_subscription:
        logger.warning(f"[{payment_id}] A ativação da assinatura falhou ou já estava ativa.")
        return

    # Envia links de acesso para o usuário que pagou
    telegram_user_id = activated_subscription.get('user', {}).get('telegram_user_id')
    if telegram_user_id:

        # --- CANCELAMENTO DOS LEMBRETES ---
        job_queue = bot_app.job_queue
        for i in range(1, 4):
            jobs = job_queue.get_jobs_by_name(f"reminder{i}_{telegram_user_id}")
            for job in jobs:
                job.schedule_removal()
                logger.info(f"Removendo job de lembrete '{job.name}' para o usuário {telegram_user_id} que acabou de pagar.")
        # --- FIM DO CANCELAMENTO ---

        logger.info(f"[{payment_id}] Assinatura ativada. Agendando envio de links para o usuário {telegram_user_id}.")
        asyncio.create_task(send_access_links(bot_app.bot, telegram_user_id, payment_id))

    else:
        logger.error(f"[{payment_id}] CRÍTICO: Assinatura ativada, mas não foi possível encontrar o telegram_user_id associado.")

    # --- NOVA LÓGICA DE RECOMPENSA DE INDICAÇÃO ---
    external_ref = activated_subscription.get('external_reference', '')
    if 'referrer_db_id' in external_ref:
        try:
            # Extrai os dados da referência da string
            parts = {p.split(':')[0]: p.split(':')[1] for p in external_ref.split(';')}
            referrer_db_id = int(parts['referrer_db_id'])
            ref_code = parts['ref_code']
            referred_user_db_id = int(parts['user_db_id'])

            # 1. Cria o registro da indicação bem-sucedida
            referral_record = await db.create_referral_record(referrer_db_id, referred_user_db_id, ref_code)
            if not referral_record:
                logger.error(f"[{payment_id}] Falha ao criar registro de indicação para referrer {referrer_db_id}.")
                return

            # 2. Concede a recompensa (7 dias) e marca como concedida
            success = await db.grant_referral_reward(referral_record['id'], referrer_db_id)
            if not success:
                logger.error(f"[{payment_id}] Falha ao conceder recompensa para referrer {referrer_db_id}.")
                return

            # 3. Notifica o usuário que indicou
            referrer_user_data = await db.find_user_by_db_id(referrer_db_id)
            if referrer_user_data and referrer_user_data.get('telegram_user_id'):
                referrer_tg_id = referrer_user_data['telegram_user_id']
                await bot_app.bot.send_message(
                    chat_id=referrer_tg_id,
                    text="🎉 Ótimas notícias! Alguém usou seu código de indicação e você acaba de ganhar *7 dias de acesso grátis*!\n\nSua assinatura foi estendida.",
                    parse_mode=ParseMode.MARKDOWN
                )
                logger.info(f"[{payment_id}] Notificação de recompensa enviada com sucesso para o usuário {referrer_tg_id}.")
        except Exception as e:
            logger.error(f"[{payment_id}] Falha CRÍTICA ao processar recompensa de indicação: {e}", exc_info=True)

# Remarketing pós-degustação
async def send_first_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Envia o primeiro lembrete 3 horas após o fim da degustação."""
    user_id = context.job.user_id
    logger.info(f"Enviando primeiro lembrete pós-trial para o usuário {user_id}.")

    # Busca os produtos para criar os botões
    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
    product_lifetime = await db.get_product_by_id(PRODUCT_ID_LIFETIME)
    if not product_monthly or not product_lifetime:
        logger.error(f"Não foi possível enviar lembrete para {user_id}: produtos não encontrados.")
        return

    text = (
    "Olá! 👋\n\n"
    "Percebi que você deu uma olhadinha nos nossos canais VIP com a degustação gratuita de 30 minutos... e que tal transformar esses 30 minutos em *prazer ilimitado*? 😏\n\n"
    "Você já sentiu o gostinho do que temos: *ANAL PROFISSIONAL*, *VIP BRASIL*, *TRANS*, *MILFs*, *vazados quentes* e muito mais... agora imagine isso *todo dia*, com atualizações frescas e acesso total!\n\n"
    "Não deixe o desejo passar... garanta já seu acesso definitivo com pagamento seguro via PIX e privacidade total. Escolha seu plano!"
    )
    keyboard = [
        [InlineKeyboardButton(f"✅ Assinatura Mensal (R$ {product_monthly['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_MONTHLY}')],
        [InlineKeyboardButton(f"💎 Acesso Vitalício (R$ {product_lifetime['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_LIFETIME}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Não foi possível enviar o primeiro lembrete para {user_id}: {e}")

async def send_second_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Envia o segundo lembrete 5 horas após o fim da degustação."""
    user_id = context.job.user_id
    logger.info(f"Enviando segundo lembrete pós-trial para o usuário {user_id}.")

    text = "Ainda está por aqui? 🤔 Lembre-se que com o acesso completo, você não perde nenhuma novidade. A oportunidade está a um clique de distância!"
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
    except Exception as e:
        logger.warning(f"Não foi possível enviar o segundo lembrete para {user_id}: {e}")

async def send_third_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Envia o terceiro e último lembrete 7 horas após o fim da degustação."""
    user_id = context.job.user_id
    logger.info(f"Enviando terceiro lembrete pós-trial para o usuário {user_id}.")

    text = (
    "Última chance, amigo! 🔥\n\n"
    "Agora que sua degustação acabou... e com ela, o acesso aos canais mais *quentes* da internet: brasileiras famosas, cenas reais de amadores, hentai sem censura, Caroline Zalog em ação e muito mais!\n\n"
    "Não fique só na vontade. Clique em /start agora, escolha seu plano (mensal ou vitalício) e mergulhe de vez no prazer que você já provou que ama. "
    "O próximo vídeo *explosivo* está te esperando! 💦"
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Não foi possível enviar o terceiro lembrete para {user_id}: {e}")


async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler para verificar novos membros em tempo real."""
    result = update.chat_member
    if not result:
        return

    # Extrai as informações relevantes do evento
    chat = result.chat
    user = result.new_chat_member.user
    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status if result.old_chat_member else None

    # Ignora eventos do próprio bot para evitar loops
    if user.id == context.bot.id:
        return

    # O evento que nos interessa é quando um usuário ENTRA no grupo.
    # Isso acontece quando o status antigo não era 'member' e o novo é.
    if new_status == 'member' and old_status not in ['member', 'administrator', 'creator']:
        logger.info(f"[GATEKEEPER] Usuário {user.id} entrou no grupo {chat.id} ('{chat.title}'). Verificando assinatura...")

        # Verifica no banco de dados se o usuário tem uma assinatura ativa
        subscription = await db.get_user_active_subscription(user.id)

        if not subscription:
            logger.warning(f"[GATEKEEPER] ACESSO NEGADO para {user.id} no grupo {chat.id}. Nenhuma assinatura ativa encontrada. Removendo...")
            try:
                # Remove o usuário do grupo
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                # Desbane imediatamente para que ele possa assinar e entrar de novo no futuro
                await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)

                # Opcional: Notifica o usuário sobre a remoção
                try:
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=f"Olá! Vimos que você tentou entrar em um de nossos canais, mas não encontramos uma assinatura ativa para você. Para obter acesso, use o comando /start e escolha um de nossos planos."
                    )
                except (Forbidden, BadRequest):
                    logger.info(f"[GATEKEEPER] Não foi possível notificar o usuário {user.id} sobre a remoção (bloqueou o bot).")

            except Forbidden:
                logger.error(f"[GATEKEEPER] CRÍTICO: Sem permissão para remover o usuário {user.id} do grupo {chat.id}. Verifique as permissões do bot no grupo.")
            except Exception as e:
                logger.error(f"[GATEKEEPER] Erro ao remover usuário {user.id} do grupo {chat.id}: {e}")
        else:
            logger.info(f"[GATEKEEPER] ACESSO PERMITIDO para {user.id} no grupo {chat.id}. Assinatura ativa encontrada.")


# --- WEBHOOKS E CICLO DE VIDA ---

# --- ESTADO PARA CONVERSATION HANDLER DE CUPOM DE USUÁRIO ---
GETTING_COUPON_CODE = 0

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

bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_coupon_code_message))

bot_app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))

# 3. Comandos regulares
#bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("status", status_command))
bot_app.add_handler(CommandHandler("renovar", renew_command))
bot_app.add_handler(CommandHandler("suporte", support_command))
bot_app.add_handler(CommandHandler("meuslinks", meuslinks_command))
bot_app.add_handler(CommandHandler("indicar", indicar_command))
bot_app.add_handler(CommandHandler("testanimation", test_animation_command))
bot_app.add_handler(CommandHandler("getid", get_id_command))

bot_app.add_handler(CommandHandler("getstate", get_state_command))

# --- SISTEMA DE MENUS INTERATIVOS ---
register_menu_handlers(bot_app)
logger.info("✅ Sistema de menus interativos registrado com sucesso!")

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
    await db.initialize_default_settings()

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
