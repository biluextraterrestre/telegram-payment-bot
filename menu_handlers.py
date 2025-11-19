# --- START OF FILE menu_handlers.py ---
"""
Sistema de Menus Interativos - Navegação 100% por botões
Todas as interações do bot são feitas através de menus com botões inline.
"""

import os
import logging
from datetime import datetime, timezone, timedelta

# importações necessárias para o pagamento
import httpx
import json
import uuid
import base64
import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram import User as TelegramUser # Importação para type hint

import db_supabase as db
from utils import format_date_br, TIMEZONE_BR, send_access_links, alert_admins
from content import CHANNEL_DESCRIPTION_TEXT

logger = logging.getLogger(__name__)

# === CONSTANTES ===
TRIAL_PRODUCT_ID = int(os.getenv("TRIAL_PRODUCT_ID", 3))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))
WELCOME_ANIMATION_FILE_ID = os.getenv("WELCOME_ANIMATION_FILE_ID")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
NOTIFICATION_URL = f"{os.getenv('WEBHOOK_BASE_URL')}/webhook/mercadopago"

# === EMOJIS PARA MELHOR VISUAL ===
EMOJI = {
    'home': '🏠',
    'status': '📊',
    'buy': '💳',
    'renew': '🔄',
    'support': '💬',
    'info': 'ℹ️',
    'referral': '🎁',
    'back': '◀️',
    'check': '✅',
    'cross': '❌',
    'clock': '⏰',
    'gift': '🎉',
    'link': '🔗',
    'groups': '👥',
    'trial': '🆓',
    'channels': '📢'
}

# === NOVO MENU INICIAL SIMPLIFICADO ===
async def show_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe o menu inicial simplificado com o foco na conversão."""
    menu_text = (
        "Seja bem-vindo(a)! 🔥\n\n"
        "Clique no botão abaixo para ver nossos planos e ter acesso imediato ao conteúdo."
    )
    keyboard = [
        # Botão 1: Call-to-action principal
        [InlineKeyboardButton("🔥 QUERO ASSINAR!", callback_data='menu_view_plans')],

        # Botão 2: Acesso ao menu completo
        [InlineKeyboardButton("ℹ️ Outras Opções (Suporte, Cupons, etc)", callback_data='menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Verifica se a mensagem veio de um comando /start ou de um clique de botão
    if update.message:
        await update.message.reply_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text=menu_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

# === MENU PRINCIPAL ===
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    """
    Exibe o menu principal. O botão de degustação é exibido condicionalmente.
    """
    user = update.effective_user
    menu_text = (
        f"Use o menu abaixo para navegar pelas opções:"
    )

    # 1. Busca a configuração da oferta de degustação no banco de dados.
    trial_setting = await db.get_setting('trial_offer')
    is_trial_enabled = trial_setting and trial_setting.get('enabled', False)

    # 2. Monta o teclado dinamicamente.
    keyboard = [
        [InlineKeyboardButton(f"🔥 QUERO ASSINAR!", callback_data='menu_view_plans')],
    ]

    # Cria a segunda linha de botões
    plans_row = [InlineKeyboardButton(f"{EMOJI['status']} Minha Assinatura", callback_data='menu_subscription_status')]
    # Adiciona o botão de degustação APENAS se estiver ativado
    if is_trial_enabled:
        plans_row.append(InlineKeyboardButton(f"{EMOJI['trial']} Testar Grátis", callback_data='menu_trial'))
    keyboard.append(plans_row)

    # Adiciona o restante dos botões
    keyboard.extend([
        [InlineKeyboardButton("🎫 Cupons Disponíveis", callback_data='menu_coupons')],
        [InlineKeyboardButton(f"{EMOJI['channels']} Sobre os Canais", callback_data='menu_show_channels')],
        [InlineKeyboardButton(f"{EMOJI['groups']} Meus Canais", callback_data='menu_my_channels')],
        [InlineKeyboardButton(f"{EMOJI['referral']} Programa de Indicação", callback_data='menu_referral')],
        [
            InlineKeyboardButton(f"{EMOJI['support']} Solicitar Suporte", callback_data='menu_support'),
            InlineKeyboardButton(f"{EMOJI['info']} Informações", callback_data='menu_info')
        ]
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text=menu_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text=menu_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# === HANDLER DO COMANDO /start ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /start completo:
    1. Envia animação com a primeira mensagem.
    2. Envia a descrição detalhada dos canais.
    3. Envia o menu principal interativo.
    """
    tg_user = update.effective_user
    await db.get_or_create_user(tg_user)

    # --- MENSAGEM 1: ANIMAÇÃO E CAPTION INICIAL ---
    welcome_caption = (
        f"Olá, *{tg_user.first_name}*!\n\n"
        f"*Bem-vindo ao nosso Bot VIP de Conteúdo Adulto (+18!)* 🔥\n\n"
        f"Aqui, você acessa o *melhor* do entretenimento erótico premium, com canais exclusivos cheios de vídeos quentes e conteúdos que vão te deixar sem fôlego. Tudo administrado de forma *segura* e *discreta* pelo nosso bot – basta pagar uma taxa acessível e entrar no *paraíso do prazer ilimitado*!\n\n"
    )
    try:
        await update.message.reply_animation(
            animation=WELCOME_ANIMATION_FILE_ID,
            caption=welcome_caption,
            parse_mode=ParseMode.MARKDOWN
        )
    except BadRequest as e:
        logger.error(f"Falha ao enviar animação: {e}. Enviando mensagem de texto.")
        await update.message.reply_text(welcome_caption, parse_mode=ParseMode.MARKDOWN)

    # --- MENSAGEM 2: MENU SIMPLIFICADO ---
    await show_start_menu(update, context)

# FUNÇÃO PARA EXIBIR A DESCRIÇÃO DOS CANAIS
async def show_channel_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe a descrição detalhada dos canais VIP."""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=CHANNEL_DESCRIPTION_TEXT,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# FUNÇÃO PARA ENVIAR LINKS
async def handle_get_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica a assinatura e inicia o envio dos links de acesso ao usuário."""
    query = update.callback_query
    user_id = update.effective_user.id

    # A mensagem editada agora serve apenas como um feedback de "carregando"
    await query.edit_message_text("📬 Gerando seus novos links, um momento...")

    user_db = await db.get_user_by_telegram_id(user_id)
    if not user_db:
        await query.edit_message_text("❌ Erro ao buscar suas informações.")
        return

    subscription = await db.get_active_subscription(user_db['id'])

    if subscription:
        # Apenas chama a função. A confirmação e o botão virão de lá.
        await send_access_links(context.bot, user_id, subscription.get('mp_payment_id', 'manual_request'), access_type='support')
    else:
        await query.edit_message_text(
            "❌ Você não possui uma assinatura ativa no momento."
        )

async def show_available_coupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe os cupons de desconto ativos."""
    query = update.callback_query
    await query.answer()
    coupons = await db.get_all_coupons(include_inactive=False)
    if not coupons:
        text = "😕 Nenhum cupom de desconto disponível no momento."
    else:
        text = "🎫 *Cupons de Desconto Ativos*\n\n"
        for coupon in coupons:
            code = coupon['code']
            discount_text = f"{int(coupon['discount_value'])}%" if coupon['discount_type'] == 'percentage' else f"R$ {coupon['discount_value']:.2f}"
            text += f"• `{code}` - *{discount_text} de desconto*\n"
        text += "\nPara usar, use o comando /cupom ou clique em 'Ver Planos' e 'Tenho um Cupom'."
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')],
        [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# === MENU: STATUS DA ASSINATURA ===
async def show_subscription_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra o status atual da assinatura do usuário"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    # Busca informações do usuário e assinatura ativa
    user_db = await db.get_user_by_telegram_id(user_id)
    if not user_db:
        await query.edit_message_text(
            text="❌ Usuário não encontrado no sistema. Use /start para começar.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return

    subscription = await db.get_active_subscription(user_db['id'])

    if not subscription:
        text = (
            "📭 *Nenhuma assinatura ativa*\n\n"
            "Você ainda não possui uma assinatura ativa.\n"
            "Escolha um dos nossos planos para começar!"
        )
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['trial']} Testar Grátis (30 min)", callback_data='menu_trial')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    else:
        # Busca informações do produto
        product = await db.get_product_by_id(subscription['product_id'])

        # Calcula dias restantes
        end_date = datetime.fromisoformat(subscription['end_date'])
        days_left = (end_date - datetime.now(TIMEZONE_BR)).days

        # Status visual
        if days_left > 7:
            status_emoji = EMOJI['check']
            status_text = "Ativa"
        elif days_left > 0:
            status_emoji = EMOJI['clock']
            status_text = f"Expira em {days_left} dia(s)"
        else:
            status_emoji = EMOJI['cross']
            status_text = "Expirada"

        text = (
            f"📊 *Status da Assinatura*\n\n"
            f"🏷️ *Plano:* {product['name']}\n"
            f"{status_emoji} *Status:* {status_text}\n"
            f"📅 *Início:* {format_date_br(subscription['start_date'])}\n"
            f"📅 *Término:* {format_date_br(subscription['end_date'])}\n"
        )

        if subscription.get('original_price') and subscription.get('final_price'):
            if subscription['original_price'] != subscription['final_price']:
                text += f"\n💰 *Valor Pago:* R$ {subscription['final_price']:.2f} (desconto aplicado)"
            else:
                text += f"\n💰 *Valor Pago:* R$ {subscription['final_price']:.2f}"

        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['renew']} Renovar/Estender", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['link']} Obter Links dos Grupos", callback_data='menu_get_links')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: VER PLANOS ===
async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe todos os planos disponíveis para compra"""
    query = update.callback_query
    await query.answer()

    # Busca todos os produtos ativos
    products = await db.get_all_products()

    if not products:
        await query.edit_message_text(
            text="❌ Nenhum plano disponível no momento. Tente novamente mais tarde.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')
            ]])
        )
        return

    text = "💳 *Planos Disponíveis*\n\nEscolha o plano ideal para você:\n\n"

    keyboard = []

    for product in products:
        # Pula o produto de trial
        if product['id'] == TRIAL_PRODUCT_ID:
            continue

        # Descrição do produto
        duration_text = ""
        if product.get('duration_days'):
            if product['duration_days'] >= 365:
                duration_text = f" ({product['duration_days'] // 365} ano(s))"
            else:
                duration_text = f" ({product['duration_days']} dias)"

        recurrent_text = " 🔄" if product.get('is_recurrent') else ""

        text += f"• *{product['name']}*{duration_text}{recurrent_text}\n"
        text += f"  💵 R$ {product['price']:.2f}\n\n"

        # Botão para este produto
        keyboard.append([
            InlineKeyboardButton(
                f"✅ Assinar - {product['name']} (R$ {product['price']:.2f})",
                callback_data=f'pay_{product["id"]}'
            )
        ])

    # Opção de usar cupom
    keyboard.append([
        InlineKeyboardButton(
            f"🎫 Tenho um Cupom de Desconto",
            callback_data='menu_apply_coupon'
        )
    ])

    keyboard.append([
        InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: TRIAL/DEGUSTAÇÃO ===
async def show_trial_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe informações sobre o período de degustação"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_db = await db.get_user_by_telegram_id(user_id)

    if not user_db:
        await query.edit_message_text(
            text="❌ Erro ao buscar suas informações. Use /start para começar.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return

    # Verifica se já usou o trial
    has_trial = await db.user_has_trial_subscription(user_db['id'])

    if has_trial:
        text = (
            "⚠️ *Degustação já utilizada*\n\n"
            "Você já utilizou seu período de degustação gratuita.\n"
            "Para continuar aproveitando nosso conteúdo, escolha um dos nossos planos!"
        )
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    else:
        text = (
            f"{EMOJI['trial']} *Período de Degustação Gratuito*\n\n"
            f"🎁 Experimente gratuitamente por *30 minutos*!\n\n"
            f"📌 *O que você recebe:*\n"
            f"• Acesso a todos os nossos grupos\n"
            f"• Conteúdo completo sem restrições\n"
            f"• Veja na prática o valor do nosso serviço\n\n"
            f"⏰ O período começa assim que você confirmar.\n"
            f"Após 30 minutos, você poderá escolher um plano para continuar."
        )
        keyboard = [
            [InlineKeyboardButton(
                f"{EMOJI['gift']} Começar Degustação Agora",
                callback_data='confirm_trial'
            )],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: MEUS CANAIS ===
async def show_my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra os grupos que o usuário tem acesso"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_db = await db.get_user_by_telegram_id(user_id)

    if not user_db:
        await query.edit_message_text(
            text="❌ Erro ao buscar suas informações.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return

    # Verifica se tem assinatura ativa
    subscription = await db.get_active_subscription(user_db['id'])

    if not subscription:
        text = (
            "📭 *Você não possui acesso ativo*\n\n"
            "Para acessar nossos grupos exclusivos, você precisa de uma assinatura ativa.\n"
            "Experimente grátis por 30 minutos ou escolha um plano!"
        )
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['trial']} Testar Grátis", callback_data='menu_trial')],
            [InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    else:
        # Calcula dias restantes
        end_date = datetime.fromisoformat(subscription['end_date'])
        time_left = end_date - datetime.now(TIMEZONE_BR)

        if time_left.days > 0:
            time_text = f"{time_left.days} dia(s)"
        else:
            hours_left = time_left.seconds // 3600
            minutes_left = (time_left.seconds % 3600) // 60
            time_text = f"{hours_left}h {minutes_left}min"

        text = (
            f"{EMOJI['groups']} *Seus Grupos de Acesso*\n\n"
            f"✅ Você tem acesso ativo!\n"
            f"⏰ Tempo restante: {time_text}\n\n"
            f"Clique no botão abaixo para receber os links de acesso a todos os grupos.\n\n"
            f"⚠️ *Importante:*\n"
            f"• Os links expiram em 2 horas\n"
            f"• Cada link só pode ser usado uma vez\n"
            f"• Entre em até 3 grupos por vez (aguarde ~30min entre lotes)"
        )
        keyboard = [
            [InlineKeyboardButton(
                f"{EMOJI['link']} Enviar Links de Acesso",
                callback_data='menu_get_links'
            )],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: PROGRAMA DE INDICAÇÃO ===
async def show_referral_program(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra informações do programa de indicação"""
    query = update.callback_query
    await query.answer()

    # Primeiro, definimos a variável 'user' com os dados de quem clicou no botão.
    user = update.effective_user

    user_db = await db.get_user_by_telegram_id(user.id)

    if not user_db:
        await query.edit_message_text(
            text="❌ Erro ao buscar suas informações.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return

    # Agora o restante do código funciona, pois a variável 'user' existe.
    referral_code = f"REF{user.id}"
    await db.ensure_referral_code_exists(user.id, referral_code)

    # Busca estatísticas de indicações
    referrals_count = await db.count_user_referrals(user_db['id'])

    text = (
        f"{EMOJI['referral']} *Programa de Indicação*\n\n"
        f"Indique amigos e ganhe *7 dias de acesso grátis* para cada amigo que assinar!\n\n"
        f"📋 *Seu código:* `{referral_code}`\n"
        f"👥 *Indicações convertidas:* {referrals_count}\n\n"
        f"🎁 *Como funciona:*\n"
        f"1. Compartilhe seu código com amigos.\n"
        f"2. Eles usam o código no comando /cupom.\n"
        f"3. Você ganha 7 dias de acesso quando eles assinarem!\n\n"
    )

    share_text = f"Use o código {referral_code} no bot para ganhar benefícios!"
    keyboard = [
        [InlineKeyboardButton(
            "📤 Compartilhar Código",
            url=f"https://t.me/share/url?text={share_text}"
        )],
        [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: SUPORTE ===
async def show_support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu de opções de suporte"""
    query = update.callback_query
    await query.answer()

    text = (
        f"{EMOJI['support']} *Central de Suporte*\n\n"
        f"Como podemos ajudar você hoje?\n\n"
        f"Escolha uma das opções abaixo:"
    )

    keyboard = [
        [InlineKeyboardButton(
            "🔗 Reenviar Links dos Grupos",
            callback_data='support_resend_links'
        )],
        [InlineKeyboardButton(
            "💳 Problema com Pagamento",
            callback_data='support_payment_issue'
        )],
        [InlineKeyboardButton(
            "❓ Outras Dúvidas",
            callback_data='support_other'
        )],
        [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_support_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra informações de ajuda com pagamento."""
    query = update.callback_query
    await query.answer()

    usuario_suporte = "@sirigueijo"
    texto = (
        f"💡 *Ajuda com Pagamento*\n\n"
        f"Se você teve algum problema com o pagamento via PIX automático, "
        f"por favor, entre em contato com nosso suporte para resolvermos rapidamente.\n\n"
        f"➡️ Contato: {usuario_suporte}"
    )
    keyboard = [[InlineKeyboardButton(f"◀️ Voltar", callback_data='menu_support')]]
    await query.edit_message_text(text=texto, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


async def handle_support_other(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra informações para outras dúvidas."""
    query = update.callback_query
    await query.answer()

    usuario_suporte = "@sirigueijo" # Se fosse "@seu_usuario_de_suporte", o correto seria "@seu\\_usuario\\_de\\_suporte"

    texto = (
        f"❓ *Outras Dúvidas*\n\n"
        f"Para qualquer outra questão, sugestão ou problema, "
        f"fale diretamente com nosso suporte.\n\n"
        f"➡️ Contato: {usuario_suporte}"
    )
    keyboard = [[InlineKeyboardButton(f"◀️ Voltar", callback_data='menu_support')]]
    await query.edit_message_text(text=texto, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# === MENU: INFORMAÇÕES ===
async def show_info_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe informações gerais sobre o serviço"""
    query = update.callback_query
    await query.answer()

    text = (
        f"{EMOJI['info']} *Informações do Serviço*\n\n"
        f"📚 *Sobre nós:*\n"
        f"Oferecemos acesso a canais exclusivos com conteúdo premium de alta qualidade.\n\n"
        f"❓ *Dúvidas Frequentes:*\n\n"
        f"*• Quanto tempo dura minha assinatura?*\n"
        f"  Depende do plano escolhido. Veja em 'Ver Planos'.\n\n"
        f"*• Como renovo minha assinatura?*\n"
        f"  Acesse 'Minha Assinatura' > 'Renovar'.\n\n"
        f"*• Não recebi os links dos grupos*\n"
        f"  Use 'Solicitar Suporte' > 'Reenviar Links'.\n\n"
        f"💬 *Precisa de mais ajuda?*\n"
        f"Use nossa Central de Suporte!"
    )

    keyboard = [
        [InlineKeyboardButton(f"{EMOJI['support']} Central de Suporte", callback_data='menu_support')],
        [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === HANDLER PARA APLICAR CUPOM ===
async def show_coupon_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Instrui o usuário a enviar o código do cupom"""
    query = update.callback_query
    await query.answer()

    text = (
        "🎫 *Usar Cupom de Desconto*\n\n"
        "Digite o código do seu cupom na próxima mensagem.\n\n"
        "Exemplo: `DESCONTO10`\n\n"
        "Após validar o cupom, você poderá escolher seu plano com o desconto aplicado."
    )

    keyboard = [[InlineKeyboardButton(f"{EMOJI['back']} Cancelar", callback_data='menu_view_plans')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

    # Define o estado para aguardar o cupom
    context.user_data['awaiting_coupon'] = True

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

# === HANDLER DE CALLBACKS ===
async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router principal para todos os callbacks de menu"""
    query = update.callback_query
    await query.answer() # Responde ao clique imediatamente

    data = query.data
    tg_user = update.effective_user
    chat_id = query.message.chat_id

    # --- LÓGICA DE NAVEGAÇÃO DO MENU ---
    if data == 'menu_main':
        await show_main_menu(update, context, edit=True)
    elif data == 'menu_subscription_status':
        await show_subscription_status(update, context)
    elif data == 'menu_view_plans':
        await show_plans(update, context)
    elif data == 'menu_trial':
        await show_trial_info(update, context)
    elif data == 'menu_my_channels':
        await show_my_channels(update, context)
    elif data == 'menu_referral':
        await show_referral_program(update, context)
    elif data == 'menu_support':
        await show_support_menu(update, context)
    elif data == 'support_resend_links':
        await handle_get_links(update, context)
    elif data == 'support_payment_issue':
        await handle_support_payment(update, context)
    elif data == 'support_other':
        await handle_support_other(update, context)
    elif data == 'menu_info':
        await show_info_menu(update, context)
    elif data == 'menu_apply_coupon':
        await show_coupon_input(update, context)
    elif data == 'menu_show_channels':
        await show_channel_description(update, context)
    elif data == 'menu_coupons':
        await show_active_coupons(update, context)
    elif data == 'menu_get_links':
        await handle_get_links(update, context)
    elif data.startswith('copy_coupon_'):
        await handle_copy_coupon(update, context)

    # --- LÓGICA DE PAGAMENTO E AÇÕES ---
    elif data.startswith('pay_'):
        product_id = int(data.split('_')[1])
        product = await db.get_product_by_id(product_id)
        if not product:
            await query.edit_message_text(text="Desculpe, este produto não está mais disponível.")
            return

        active_coupon = context.user_data.get('active_coupon')
        referral_info = context.user_data.get('referral_info')
        final_price = product['price']
        original_price = product['price']

        if active_coupon:
            discount_type = active_coupon['discount_type']
            discount_value = active_coupon['discount_value']
            if discount_type == 'percentage':
                final_price = original_price * (1 - discount_value / 100)
            else:
                final_price = max(0, original_price - discount_value)

        if final_price < 0.01:
            # Lógica de cupom 100%
            await query.edit_message_text(text=f"✅ Cupom de 100% aplicado! Liberando seu acesso...")
            db_user = await db.get_or_create_user(tg_user)
            notes = f"cupom_100%_{active_coupon['code'] if active_coupon else 'FREE'}"
            new_subscription = await db.grant_or_extend_manual_subscription(db_user['id'], product['id'], notes)
            if new_subscription:
                await send_access_links(context.bot, tg_user.id, notes, access_type='purchase')
            else:
                await query.edit_message_text("❌ Erro ao liberar seu acesso. Contate o suporte.")
            context.user_data.clear()
            return

        if active_coupon:
            await query.edit_message_text(text=f"✅ Cupom aplicado! Gerando PIX com desconto...")
        else:
            await query.edit_message_text(text=f"Gerando sua cobrança PIX, aguarde...")

        payment_data = await create_pix_payment(tg_user, product, final_price, active_coupon, referral_info)

        if payment_data:
            qr_code_image = base64.b64decode(payment_data['qr_code_base64'])
            image_stream = io.BytesIO(qr_code_image)
            await context.bot.send_photo(chat_id=chat_id, photo=image_stream, caption="Use o QR Code acima ou o código abaixo para pagar.")
            await context.bot.send_message(chat_id=chat_id, text=f"PIX Copia e Cola:\n\n`{payment_data['pix_copy_paste']}`", parse_mode=ParseMode.MARKDOWN_V2)
            await context.bot.send_message(chat_id=chat_id, text="✅ Pagamento confirmado? Você receberá os links de acesso em instantes!")
            context.user_data.clear()
        else:
            await query.edit_message_text(text="❌ Erro ao gerar sua cobrança. Tente novamente ou contate o suporte.")

    elif data == 'confirm_trial':
        # Lógica de degustação
        db_user = await db.get_or_create_user(tg_user)
        active_sub = await db.get_user_active_subscription(tg_user.id)
        if active_sub:
            await query.edit_message_text("Você já possui uma assinatura ativa!")
            return

        can_start_trial = await db.check_and_set_trial_used(db_user['id'])
        if can_start_trial:
            await query.edit_message_text("✅ Gerando seu acesso de degustação...")
            trial_sub = await db.create_trial_subscription(db_user['id'])
            if trial_sub:
                await send_access_links(context.bot, tg_user.id, trial_sub['mp_payment_id'], access_type='trial')
        else:
            await query.edit_message_text("❌ Você já utilizou seu período de degustação.")

# === MENU: CUPONS ATIVOS ===
async def show_active_coupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra todos os cupons ativos disponíveis"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_db = await db.get_user_by_telegram_id(user_id)

    if not user_db:
        await query.edit_message_text(
            text="❌ Erro ao buscar suas informações.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return

    # Busca cupons ativos (usando a função que corrigimos anteriormente)
    coupons = await db.get_active_coupons()

    if not coupons:
        text = (
            "🎫 *Cupons Disponíveis*\n\n"
            "No momento não há cupons ativos disponíveis.\n\n"
            "📢 Fique atento! Novos cupons são disponibilizados regularmente.\n"
        )
        keyboard = [[InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]]
    else:
        text = "🎫 *Cupons Disponíveis*\n\n"
        text += "Copie o código e use na hora de comprar para ganhar desconto!\n\n"

        keyboard = []

        for coupon in coupons:
            # Verifica se o usuário já usou este cupom
            already_used = await db.check_user_used_coupon(user_db['id'], coupon['id'])

            # Calcula informações do cupom
            if coupon['discount_type'] == 'percentage':
                discount_text = f"{int(coupon['discount_value'])}%"
            else:
                discount_text = f"R$ {coupon['discount_value']:.2f}"

            # --- INÍCIO DA CORREÇÃO ---
            # Validade (Lógica aprimorada para lidar com cupons sem data de expiração)
            valid_until_str = coupon.get('valid_until')
            if valid_until_str:
                valid_until = datetime.fromisoformat(valid_until_str)
                days_left = (valid_until - datetime.now(TIMEZONE_BR)).days

                if days_left < 0:
                    validity_text = "📅 Expirado"
                elif days_left == 0:
                    validity_text = "⏰ Expira hoje!"
                elif days_left == 1:
                    validity_text = "⏰ Expira amanhã!"
                else:
                    validity_text = f"📅 Válido por mais {days_left} dias"
            else:
                # Caso o cupom não tenha data de expiração (valid_until é None)
                validity_text = "📅 Válido indefinidamente"
            # --- FIM DA CORREÇÃO ---


            # Usos disponíveis
            usage_limit = coupon.get('usage_limit')
            if usage_limit:
                uses_left = usage_limit - coupon.get('usage_count', 0)
                usage_text = f"🎟️ {uses_left} usos restantes"
            else:
                usage_text = "🎟️ Usos ilimitados"

            # Status de uso do usuário
            if already_used:
                status_emoji = "✅"
                status_text = "(Você já usou)"
            else:
                status_emoji = "🆕"
                status_text = "(Disponível)"

            # Adiciona ao texto
            text += (
                f"{status_emoji} *{coupon['code']}*\n"
                f"💰 Desconto: *{discount_text}*\n"
                f"{validity_text} | {usage_text}\n"
                f"_{status_text}_\n\n"
            )

            # Adiciona botão para copiar (se não usou)
            if not already_used:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📋 Copiar Cupom: {coupon['code']}",
                        callback_data=f"copy_coupon_{coupon['code']}"
                    )
                ])

        # Informação de como usar
        text += (
            "\n💡 *Como usar:*\n"
            "1. Copie o código do cupom.\n"
            "2. Volte ao menu e clique em 'Ver Planos'.\n"
            "3. Clique em 'Tenho um Cupom de Desconto'.\n"
            "4. Envie o código na mensagem.\n"
            "5. Pronto! O desconto será aplicado."
        )

        keyboard.append([InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')])
        keyboard.append([InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def handle_copy_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler para quando usuário clica em 'Copiar Cupom'.
    Envia o código em uma mensagem separada para facilitar a cópia.
    """
    query = update.callback_query
    await query.answer("✅ Código copiável enviado!")

    # Extrai o código do cupom do callback_data
    coupon_code = query.data.replace('copy_coupon_', '')

    # Envia mensagem separada com o código
    message = (
        f"🎫 *Código do Cupom:*\n\n"
        f"`{coupon_code}`\n\n"
        f"💡 Toque no código acima para copiar!\n\n"
        f"Para usar:\n"
        f"1. Vá em 'Ver Planos'\n"
        f"2. Escolha um plano\n"
        f"3. Clique em 'Tenho um Cupom'\n"
        f"4. Cole o código"
    )

    await query.message.reply_text(
        text=message,
        parse_mode=ParseMode.MARKDOWN
    )

# === REGISTRAR HANDLERS ===
def register_menu_handlers(application):
    """Registra todos os handlers de menu no bot"""
    # Comando /start
    application.add_handler(CommandHandler("start", start_command))

    # A função handle_menu_callback agora vai receber TODOS os cliques
    application.add_handler(CallbackQueryHandler(handle_menu_callback))

    logger.info("✅ Handlers de menu registrados com sucesso!")
