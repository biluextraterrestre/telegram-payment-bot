# --- admin_handlers.py (VERSÃO FINAL COMPLETA E CORRIGIDA) ---

import os
import logging
import asyncio
from functools import wraps
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.helpers import escape_markdown

import db_supabase as db
import scheduler
from utils import send_access_links, format_date_br

logger = logging.getLogger(__name__)

# --- CONFIGURAÇÕES E CONSTANTES ---
ADMIN_IDS_STR = os.getenv("ADMIN_USER_IDS", "")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',')] if ADMIN_IDS_STR else []
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))
TIMEZONE_BR = db.TIMEZONE_BR

# --- ESTADOS DA CONVERSATION HANDLER ---
states_list = [
    'SELECTING_ACTION', 'GETTING_USER_ID_FOR_CHECK', 'GETTING_USER_ID_FOR_GRANT',
    'SELECTING_PLAN_FOR_GRANT', 'GETTING_USER_ID_FOR_REVOKE', 'CONFIRMING_REVOKE',
    'GETTING_BROADCAST_MESSAGE', 'CONFIRMING_BROADCAST', 'SELECTING_NEW_GROUP',
    'CONFIRMING_NEW_GROUP_BROADCAST', 'VIEWING_STATS', 'MANAGING_GROUPS',
    'MANAGING_COUPONS', 'GETTING_COUPON_CODE', 'GETTING_COUPON_DISCOUNT',
    'GETTING_COUPON_VALIDITY', 'GETTING_COUPON_USAGE_LIMIT',
    'GETTING_GROUP_FORWARD', 'CONFIRMING_GROUP_ADD', 'GETTING_GROUP_TO_REMOVE',
    'CONFIRMING_GROUP_REMOVE', 'GETTING_COUPON_TO_DEACTIVATE', 'GETTING_COUPON_TO_REACTIVATE',
    'VIEWING_LOGS', 'GETTING_TRANSACTION_SEARCH', 'MANAGING_REFERRALS', 'FILTERING_LOGS'
]
(
    SELECTING_ACTION, GETTING_USER_ID_FOR_CHECK, GETTING_USER_ID_FOR_GRANT,
    SELECTING_PLAN_FOR_GRANT, GETTING_USER_ID_FOR_REVOKE, CONFIRMING_REVOKE,
    GETTING_BROADCAST_MESSAGE, CONFIRMING_BROADCAST, SELECTING_NEW_GROUP,
    CONFIRMING_NEW_GROUP_BROADCAST, VIEWING_STATS, MANAGING_GROUPS,
    MANAGING_COUPONS, GETTING_COUPON_CODE, GETTING_COUPON_DISCOUNT,
    GETTING_COUPON_VALIDITY, GETTING_COUPON_USAGE_LIMIT, GETTING_GROUP_FORWARD,
    CONFIRMING_GROUP_ADD, GETTING_GROUP_TO_REMOVE, CONFIRMING_GROUP_REMOVE,
    GETTING_COUPON_TO_DEACTIVATE, GETTING_COUPON_TO_REACTIVATE,
    VIEWING_LOGS, GETTING_TRANSACTION_SEARCH, MANAGING_REFERRALS, FILTERING_LOGS
) = range(len(states_list))


# --- DECORATOR DE SEGURANÇA ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            logger.warning(f"Acesso não autorizado ao painel admin pelo usuário {user_id}.")
            if update.message:
                await update.message.reply_text("Você não tem permissão para usar este comando.")
            elif update.callback_query:
                await update.callback_query.answer("Acesso negado.", show_alert=True)
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped


# --- SEÇÃO 1: MENU PRINCIPAL E NAVEGAÇÃO ---

async def show_main_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False):
    """Mostra o painel de administração principal."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Estatísticas", callback_data="admin_stats"),
            InlineKeyboardButton("🎁 Indicações", callback_data="admin_referrals")
        ],
        [
            InlineKeyboardButton("🔍 Checar Usuário", callback_data="admin_check_user"),
            InlineKeyboardButton("💳 Transações", callback_data="admin_transactions")
        ],
        [
            InlineKeyboardButton("✅ Conceder Acesso", callback_data="admin_grant_access"),
            InlineKeyboardButton("❌ Revogar Acesso", callback_data="admin_revoke_access")
        ],
        [
            InlineKeyboardButton("📢 Mensagem Global", callback_data="admin_broadcast"),
            InlineKeyboardButton("🎟️ Cupons", callback_data="admin_manage_coupons")
        ],
        [
            InlineKeyboardButton("🏢 Gerenciar Grupos", callback_data="admin_manage_groups"),
            InlineKeyboardButton("📝 Ver Logs", callback_data="admin_view_logs")
        ],
        [InlineKeyboardButton("✖️ Fechar Painel", callback_data="admin_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "👑 *Painel de Administração Avançado*\n\nSelecione uma ação:"
    try:
        if is_edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except BadRequest as e:
        if "message is not modified" not in str(e):
            logger.error(f"Erro ao editar menu principal: {e}")

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ponto de entrada para o comando /admin."""
    await show_main_admin_menu(update, context)
    return SELECTING_ACTION

@admin_only
async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback para o botão 'Voltar' que retorna ao menu principal."""
    query = update.callback_query
    await query.answer()
    await show_main_admin_menu(update, context, is_edit=True)
    return SELECTING_ACTION

@admin_only
async def back_to_manage_coupons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback para voltar ao menu de gerenciamento de cupons."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await manage_coupons_start(update, context)
    return MANAGING_COUPONS


# --- SEÇÃO 2: DASHBOARDS E VISUALIZAÇÃO ---

@admin_only
async def view_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra estatísticas detalhadas do sistema."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📊 Carregando estatísticas...")
    try:
        stats = await db.get_system_stats()
        text = (
            "📊 *Estatísticas do Sistema*\n\n"
            f"👥 *Usuários Totais:* {stats.get('total_users', 0)}\n"
            f"✅ *Assinaturas Ativas:* {stats.get('active_subscriptions', 0)}\n"
            f"⏳ *Assinaturas Pendentes:* {stats.get('pending_subscriptions', 0)}\n"
            f"❌ *Assinaturas Expiradas:* {stats.get('expired_subscriptions', 0)}\n\n"
            f"💰 *Receita Total:* R$ {stats.get('total_revenue', 0):.2f}\n"
            f"💵 *Receita (Mês):* R$ {stats.get('monthly_revenue', 0):.2f}\n"
            f"💸 *Receita (Hoje):* R$ {stats.get('daily_revenue', 0):.2f}\n\n"
            f"🏢 *Grupos Cadastrados:* {stats.get('total_groups', 0)}\n"
            f"🎟️ *Cupons Ativos:* {stats.get('active_coupons', 0)}\n\n"
            f"📈 *Taxa de Conversão:* {stats.get('conversion_rate', 0.0):.1f}%\n"
            f"📅 *Última atualização:* {datetime.now(TIMEZONE_BR).strftime('%d/%m/%Y %H:%M')}"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="admin_stats")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Erro ao carregar estatísticas: {e}", exc_info=True)
        await query.edit_message_text("❌ Erro ao carregar estatísticas.")
    return SELECTING_ACTION

@admin_only
async def manage_referrals_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra o painel de estatísticas de indicações."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🎁 Carregando estatísticas de indicações...")
    try:
        stats = await db.get_referral_stats()
        text = (
            "🎁 *Dashboard de Indicações*\n\n"
            f"👥 *Total de indicações registradas:* {stats.get('total_referrals', 0)}\n"
            f"✅ *Conversões (indicados que pagaram):* {stats.get('converted_referrals', 0)}\n"
            f"💰 *Recompensas distribuídas (total de dias):* {stats.get('rewards_granted_days', 0)} dias\n\n"
            "🏆 *Top 5 Indicadores (por conversões):*\n"
        )
        top_referrers = stats.get('top_referrers', [])
        if top_referrers:
            for i, referrer in enumerate(top_referrers, 1):
                text += f"{i}. {referrer.get('first_name', 'N/A')} (`{referrer.get('telegram_user_id', 'N/A')}`) - {referrer.get('referral_count', 0)} conversões\n"
        else:
            text += "Nenhuma conversão registrada ainda.\n"
        keyboard = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="admin_referrals")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Erro ao carregar estatísticas de indicações: {e}", exc_info=True)
        await query.edit_message_text("❌ Erro ao carregar dados de indicações.")
    return MANAGING_REFERRALS

@admin_only
async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Ponto de entrada para a visualização de logs.
    Mostra os logs com base nos filtros armazenados em context.user_data.
    """
    query = update.callback_query
    await query.answer()

    # Pega filtros da sessão ou usa padrões
    filters = context.user_data.get('log_filters', {})
    log_type = filters.get('type')
    days_ago = filters.get('days')

    await query.edit_message_text("📝 Carregando logs...")

    try:
        logs = await db.get_recent_logs(limit=20, log_type=log_type, days_ago=days_ago)

        # Monta o cabeçalho com os filtros ativos
        header = "📝 *Logs do Sistema*\n"
        if log_type or days_ago is not None:
            header += "_Filtros ativos:_\n"
            if log_type:
                header += f"  - Tipo: `{log_type}`\n"
            if days_ago is not None:
                period_map = {0: "Hoje", 1: "Últimas 24h", 7: "Últimos 7 dias"}
                header += f"  - Período: `{period_map.get(days_ago, f'Últimos {days_ago} dias')}`\n"
        header += "\n"

        text = header
        if not logs:
            text += "Nenhum log encontrado para os filtros selecionados."
        else:
            for log in logs:
                timestamp = format_date_br(log.get('created_at'))
                l_type = log.get('type', 'info').upper()

                # --- CORREÇÃO APLICADA AQUI ---
                message = log.get('message', 'N/A')
                # Escapa a mensagem para evitar erros de formatação
                escaped_message = escape_markdown(message, version=2)
                text += f"🕐 {timestamp}\n📌 `[{l_type}]` {escaped_message}\n\n"
                # --- FIM DA CORREÇÃO ---

        # --- Teclado Interativo ---
        keyboard = [
            [
                InlineKeyboardButton("🔄 Atualizar", callback_data="logs_view"),
                InlineKeyboardButton("ByType", callback_data="logs_filter_type_menu"),
                InlineKeyboardButton("ByDate", callback_data="logs_filter_date_menu")
            ],
            [InlineKeyboardButton("🧹 Limpar Filtros", callback_data="logs_filter_clear")],
            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="admin_back_to_menu")]
        ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Erro ao carregar logs: {e}", exc_info=True)
        await query.edit_message_text("❌ Erro ao carregar os logs.")

    return FILTERING_LOGS


async def logs_filter_type_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra as opções de filtro por tipo de log."""
    query = update.callback_query
    await query.answer()

    # Tipos comuns de log para facilitar a filtragem
    log_types = ['admin_action', 'error', 'user_created', 'subscription_activated', 'subscription_revoked']

    keyboard = []
    for log_type in log_types:
        keyboard.append([InlineKeyboardButton(f"'{log_type}'", callback_data=f"logs_set_type_{log_type}")])

    keyboard.append([InlineKeyboardButton("⬅️ Voltar aos Logs", callback_data="logs_view")])
    await query.edit_message_text("🔎 *Filtrar por Tipo*\n\nSelecione um tipo de log para visualizar:", reply_markup=InlineKeyboardMarkup(keyboard))
    return FILTERING_LOGS


async def logs_filter_date_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra as opções de filtro por data."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("Hoje", callback_data="logs_set_date_0")],
        [InlineKeyboardButton("Últimas 24 horas", callback_data="logs_set_date_1")],
        [InlineKeyboardButton("Últimos 7 dias", callback_data="logs_set_date_7")],
        [InlineKeyboardButton("⬅️ Voltar aos Logs", callback_data="logs_view")]
    ]
    await query.edit_message_text("📅 *Filtrar por Período*\n\nSelecione um período para visualizar:", reply_markup=InlineKeyboardMarkup(keyboard))
    return FILTERING_LOGS


async def logs_set_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Aplica um filtro (tipo ou data) e recarrega a visualização de logs."""
    query = update.callback_query
    data = query.data.split('_')
    filter_type = data[2]
    filter_value = "_".join(data[3:])

    if 'log_filters' not in context.user_data:
        context.user_data['log_filters'] = {}

    if filter_type == 'type':
        context.user_data['log_filters']['type'] = filter_value
    elif filter_type == 'date':
        context.user_data['log_filters']['days'] = int(filter_value)

    # Chama a função principal para recarregar com os novos filtros
    return await view_logs(update, context)


async def logs_clear_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Limpa todos os filtros e recarrega a visualização de logs."""
    context.user_data.pop('log_filters', None)
    return await view_logs(update, context)

# --- SEÇÃO DE GERENCIAMENTO DE GRUPOS COM LOGS DE DEBUG ---

@admin_only
async def manage_groups_start(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False) -> int:
    """Apresenta a lista de grupos cadastrados e as opções de gerenciamento."""
    query = update.callback_query

    # --- DEBUG LOGS ---
    logger.info("-> [DEBUG] Entrando em manage_groups_start...")
    admin_user_id = update.effective_user.id
    if query:
        await query.answer()
        logger.info(f"-> [DEBUG] Botão 'admin_manage_groups' clicado pelo admin {admin_user_id}.")
    # --- FIM DEBUG LOGS ---

    try:
        # --- DEBUG LOGS ---
        logger.info("-> [DEBUG] Buscando grupos no banco de dados...")
        groups = await db.get_all_groups_with_names()
        logger.info(f"-> [DEBUG] Encontrados {len(groups)} grupos.")
        # --- FIM DEBUG LOGS ---

        text = f"🏢 *Gerenciamento de Grupos*\n\n📊 Total de grupos: {len(groups)}\n\n"
        if groups:
            text += "Grupos cadastrados:\n"
            for i, group in enumerate(groups[:15], 1):
                text += f"{i}. {group.get('name', 'Sem nome')} (`{group['telegram_chat_id']}`)\n"
        else:
            text += "Nenhum grupo cadastrado no momento.\n"

        keyboard = [
            [InlineKeyboardButton("➕ Adicionar Grupo", callback_data="group_add")],
            [InlineKeyboardButton("🗑️ Remover Grupo", callback_data="group_remove")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # --- DEBUG LOGS ---
        logger.info("-> [DEBUG] Preparando para editar a mensagem do menu de grupos...")
        if is_edit and query:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif query: # Garante que o clique no botão edite a mensagem
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

        logger.info(f"-> [DEBUG] Mensagem editada. Retornando estado MANAGING_GROUPS ({MANAGING_GROUPS}).")
        # --- FIM DEBUG LOGS ---

        return MANAGING_GROUPS

    except Exception as e:
        # --- CAPTURA DE ERRO ---
        logger.error(f"❌ ERRO CRÍTICO em manage_groups_start: {e}", exc_info=True)
        error_text = f"❌ Ocorreu um erro inesperado ao tentar gerenciar os grupos. A equipe de desenvolvimento foi notificada."
        if query:
            await query.edit_message_text(error_text)
        elif update.message:
            await update.message.reply_text(error_text)
        return ConversationHandler.END # Termina a conversa para evitar travamento

# --- FLUXO: ADICIONAR GRUPO ---

@admin_only
async def add_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo para adicionar um novo grupo."""
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_groups_back")]]

    text = (
        "➕ *Adicionar Novo Grupo*\n\n"
        "1. Adicione este bot ao grupo que deseja cadastrar (ele precisa ter permissões de administrador).\n"
        "2. Encaminhe qualquer mensagem desse grupo para mim aqui.\n\n"
        "Eu irei extrair os dados do grupo automaticamente. Use /cancel para abortar."
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return GETTING_GROUP_FORWARD

@admin_only
async def add_group_receive_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processa a mensagem encaminhada para extrair dados do grupo."""
    if not update.message or not update.message.forward_from_chat:
        await update.message.reply_text("❌ Isso não parece ser uma mensagem encaminhada de um grupo. Tente novamente ou use /cancel.")
        return GETTING_GROUP_FORWARD

    chat = update.message.forward_from_chat
    if chat.type not in ['group', 'supergroup', 'channel']:
        await update.message.reply_text("❌ O encaminhamento deve ser de um grupo público ou canal. Tente novamente.")
        return GETTING_GROUP_FORWARD

    context.user_data['new_group_id'] = chat.id
    context.user_data['new_group_title'] = chat.title

    keyboard = [
        [InlineKeyboardButton("✅ Sim, Adicionar", callback_data="add_group_confirm")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="admin_manage_groups_back")]
    ]
    text = (
        "🔍 *Confirmação*\n\n"
        f"Encontrei o seguinte grupo:\n\n"
        f"📝 **Nome:** {chat.title}\n"
        f"🆔 **ID:** `{chat.id}`\n\n"
        "Deseja adicionar este grupo ao sistema?"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return CONFIRMING_GROUP_ADD

@admin_only
async def add_group_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva o novo grupo no banco de dados e volta para o menu de gerenciamento."""
    query = update.callback_query
    await query.answer()

    chat_id = context.user_data.get('new_group_id')
    chat_title = context.user_data.get('new_group_title')

    if not chat_id or not chat_title:
        await query.edit_message_text("❌ Erro: Dados do grupo não encontrados na sessão. Operação cancelada.")
    else:
        success = await db.add_group(chat_id, chat_title)
        if success:
            await query.edit_message_text(f"✅ Grupo '**{chat_title}**' adicionado com sucesso!")
            await db.create_log('admin_action', f"Admin {update.effective_user.id} adicionou o grupo {chat_title} ({chat_id})")
        else:
            await query.edit_message_text(f"❌ Erro ao adicionar o grupo. Ele pode já estar cadastrado. Verifique os logs.")

    context.user_data.clear()
    await asyncio.sleep(2)
    return await manage_groups_start(update, context, is_edit=True)

# --- FLUXO: REMOVER GRUPO ---

@admin_only
async def remove_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra os grupos cadastrados e pede para o admin escolher qual remover."""
    query = update.callback_query
    await query.answer()
    groups = await db.get_all_groups_with_names()

    if not groups:
        await query.edit_message_text("Nenhum grupo cadastrado para remover.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_groups_back")]]))
        return MANAGING_GROUPS

    keyboard = []
    for group in groups:
        keyboard.append([InlineKeyboardButton(f"🗑️ {group['name']}", callback_data=f"remove_group_{group['telegram_chat_id']}")])
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_groups_back")])

    await query.edit_message_text("🗑️ *Remover Grupo*\n\nSelecione o grupo que deseja remover da lista:", reply_markup=InlineKeyboardMarkup(keyboard))
    return GETTING_GROUP_TO_REMOVE

@admin_only
async def remove_group_confirm_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Pede a confirmação final antes de remover o grupo."""
    query = update.callback_query
    await query.answer()
    chat_id_to_remove = int(query.data.split('_')[-1])
    context.user_data['group_to_remove_id'] = chat_id_to_remove

    group = await db.get_group_by_chat_id(chat_id_to_remove)
    if not group:
        await query.edit_message_text("❌ Grupo não encontrado. Pode já ter sido removido.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_groups_back")]]))
        return MANAGING_GROUPS

    keyboard = [
        [InlineKeyboardButton("✅ SIM, REMOVER AGORA", callback_data="remove_group_confirmed")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_manage_groups_back")]
    ]
    text = (
        f"⚠️ *ATENÇÃO* ⚠️\n\n"
        f"Você tem certeza que deseja remover o grupo '**{group['name']}**' (`{group['telegram_chat_id']}`) do sistema?\n\n"
        "Esta ação **não remove** os usuários do grupo, apenas impede que novos membros recebam o link de acesso a ele."
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return CONFIRMING_GROUP_REMOVE

@admin_only
async def remove_group_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Executa a remoção do grupo do banco de dados."""
    query = update.callback_query
    await query.answer()
    chat_id = context.user_data.get('group_to_remove_id')

    if not chat_id:
        await query.edit_message_text("❌ Erro: ID do grupo não encontrado na sessão. Operação cancelada.")
    else:
        success = await db.remove_group(chat_id)
        if success:
            await query.edit_message_text("✅ Grupo removido com sucesso do sistema!")
            await db.create_log('admin_action', f"Admin {update.effective_user.id} removeu o grupo {chat_id}")
        else:
            await query.edit_message_text("❌ Erro ao remover o grupo do banco de dados. Verifique os logs.")

    context.user_data.clear()
    await asyncio.sleep(2)
    return await manage_groups_start(update, context, is_edit=True)

@admin_only
async def back_to_manage_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback para voltar ao menu de gerenciamento de grupos."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await manage_groups_start(update, context, is_edit=True)
    return MANAGING_GROUPS


# --- SEÇÃO 3: CONSULTA DE DADOS ---
# ... (todas as outras funções como check_user, grant_access, revoke, broadcast, cupons, etc. permanecem aqui) ...
# (O código foi omitido para brevidade, mas ele deve estar presente no seu arquivo)

@admin_only
async def search_transactions_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de busca de transações."""
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "💳 *Buscar Transações*\n\n"
        "Envie um dos seguintes dados:\n"
        "• ID numérico do usuário\n"
        "• @username do usuário\n"
        "• ID do pagamento (Mercado Pago)\n"
        "• `hoje` para transações do dia\n"
        "• `semana` para transações dos últimos 7 dias",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return GETTING_TRANSACTION_SEARCH

@admin_only
async def search_transactions_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Executa a busca de transações e exibe os resultados."""
    search_term = update.message.text.strip().lower()
    await update.message.reply_text(f"🔍 Buscando transações para: `{search_term}`...", parse_mode=ParseMode.MARKDOWN)
    try:
        transactions = await db.search_transactions(search_term)
        if not transactions:
            await update.message.reply_text("❌ Nenhuma transação encontrada para este termo.")
        else:
            text = f"💳 *Resultados da Busca* ({len(transactions)} encontradas)\n\n"
            for trans in transactions[:10]:
                user = trans.get('user') or {}
                product = trans.get('product') or {}
                status = trans.get('status', 'unknown')
                status_emoji = "✅" if status == 'active' else "⏳" if status == 'pending_payment' else "❌"
                text += (
                    f"{status_emoji} *{user.get('first_name', 'Usuário Removido')}* (@{user.get('username', 'N/A')})\n"
                    f"   💰 {product.get('name', 'Produto Removido')}\n"
                    f"   📅 {format_date_br(trans.get('created_at'))}\n"
                    f"   🆔 `{trans.get('mp_payment_id', 'N/A')}`\n\n"
                )
            if len(transactions) > 10:
                text += f"... e mais {len(transactions) - 10} transações."
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Erro ao buscar transações: {e}", exc_info=True)
        await update.message.reply_text("❌ Ocorreu um erro inesperado ao buscar as transações.")
    await show_main_admin_menu(update, context)
    return SELECTING_ACTION

@admin_only
async def check_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo para checar o status de um usuário."""
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="🔍 Por favor, envie o ID numérico ou o @username do usuário que deseja checar.",
        reply_markup=reply_markup
    )
    return GETTING_USER_ID_FOR_CHECK

@admin_only
async def check_user_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o identificador do usuário, busca no DB e exibe as informações."""
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)
    if not user_data:
        await update.message.reply_text("❌ Usuário não encontrado. Peça para o usuário iniciar o bot com /start e tente novamente.")
    else:
        first_name = user_data.get('first_name', 'N/A')
        tg_id = user_data.get('telegram_user_id', 'N/A')
        username = f"@{user_data['username']}" if user_data.get('username') else 'N/A'
        created_at = format_date_br(user_data.get('created_at'))
        referral_code = user_data.get('referral_code', 'Nenhum')
        message = (
            f"📊 *Status do Usuário*\n\n"
            f"👤 *Nome:* {first_name}\n"
            f"🆔 *Telegram ID:* `{tg_id}`\n"
            f"✏️ *Username:* {username}\n"
            f"🎁 *Cód. Indicação:* `{referral_code}`\n"
            f"📅 *Cadastro:* {created_at}\n\n"
            "-------------------\n"
        )
        active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)
        if active_sub:
            product_name = active_sub.get('product', {}).get('name', 'N/A')
            start_date = format_date_br(active_sub.get('start_date'))
            end_date = "Vitalício" if not active_sub.get('end_date') else format_date_br(active_sub.get('end_date'))
            mp_id = active_sub.get('mp_payment_id', 'N/A')
            message += (
                f"✅ *Assinatura Ativa*\n"
                f"📦 *Plano:* {product_name}\n"
                f"📅 *Início:* {start_date}\n"
                f"📆 *Fim:* {end_date}\n"
                f"🆔 *ID Pagamento:* `{mp_id}`"
            )
        else:
            all_subs = user_data.get('subscriptions', [])
            if all_subs:
                message += f"❌ *Nenhuma assinatura ativa*\n\n📜 Histórico: {len(all_subs)} assinatura(s) anterior(es) encontradas."
            else:
                message += "❌ *Nenhuma assinatura encontrada para este usuário.*"
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    await show_main_admin_menu(update, context)
    return SELECTING_ACTION

# ... COLE AQUI TODAS AS SUAS OUTRAS FUNÇÕES DE ADMIN (grant_access, revoke, broadcast, cupons, etc.)

@admin_only
async def grant_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo para conceder acesso manual."""
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="✅ Envie o ID numérico ou @username do usuário para conceder acesso.", reply_markup=reply_markup)
    return GETTING_USER_ID_FOR_GRANT

@admin_only
async def grant_access_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o ID do usuário e mostra as opções de plano."""
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)
    if not user_data:
        await update.message.reply_text("❌ Usuário não encontrado. Peça para o usuário iniciar o bot primeiro com /start.")
        await show_main_admin_menu(update, context)
        return SELECTING_ACTION
    context.user_data['grant_user_id'] = user_data['id']
    context.user_data['grant_telegram_user_id'] = user_data['telegram_user_id']
    keyboard = [
        [InlineKeyboardButton("📅 Assinatura Mensal", callback_data=f"grant_plan_{PRODUCT_ID_MONTHLY}")],
        [InlineKeyboardButton("💎 Acesso Vitalício", callback_data=f"grant_plan_{PRODUCT_ID_LIFETIME}")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
    ]
    await update.message.reply_text(f"✅ Usuário *{user_data['first_name']}* encontrado. Qual plano deseja conceder?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return SELECTING_PLAN_FOR_GRANT

@admin_only
async def grant_access_select_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Concede ou estende o plano selecionado ao usuário."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split('_')[-1])
    db_user_id = context.user_data.get('grant_user_id')
    telegram_user_id = context.user_data.get('grant_telegram_user_id')
    admin_id = update.effective_user.id

    if not db_user_id or not telegram_user_id:
        await query.edit_message_text("❌ Erro: Dados do usuário não encontrados na sessão. Por favor, tente novamente.")
        context.user_data.clear()
        return await back_to_main_menu(update, context)

    await query.edit_message_text(text="⏳ Processando concessão...")

    unique_grant_id = f"manual_grant_by_admin_{admin_id}_{datetime.now().timestamp()}"

    # Usa a nova função inteligente
    result_sub = await db.grant_or_extend_manual_subscription(db_user_id, product_id, unique_grant_id)

    if result_sub:
        # Tratamento do caso especial: usuário já é vitalício
        if result_sub.get("status") == "already_lifetime":
            await query.edit_message_text(text=f"✅ Operação concluída. O usuário {telegram_user_id} já possui acesso vitalício, nenhuma ação foi necessária.")
        else:
            # Caso de sucesso (criação ou extensão)
            action_log = "estendeu" if result_sub.get('start_date') != result_sub.get('updated_at') else "concedeu"
            await db.create_log('admin_action', f"Admin {admin_id} {action_log} acesso manual ({product_id}) para usuário {telegram_user_id}")

            # Envia links apenas se for uma nova concessão, não uma extensão
            if action_log == "concedeu":
                await send_access_links(context.bot, telegram_user_id, result_sub.get('mp_payment_id', 'manual'))

            await query.edit_message_text(text=f"✅ Acesso {action_log} com sucesso para o usuário {telegram_user_id}!")

            try:
                message_to_user = "🎉 Boas notícias! Sua assinatura foi estendida por um administrador." if action_log == "estendeu" else "🎉 Boas notícias! Um administrador concedeu acesso premium a você."
                await context.bot.send_message(telegram_user_id, message_to_user)
            except Exception as e:
                logger.error(f"Erro ao notificar usuário {telegram_user_id} sobre concessão/extensão: {e}")
    else:
        await query.edit_message_text(text="❌ Falha ao conceder acesso. Verifique os logs do sistema.")

    context.user_data.clear()
    await asyncio.sleep(3) # Aumenta o tempo para o admin ler a mensagem
    return await back_to_main_menu(update, context)

@admin_only
async def revoke_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo para revogar o acesso de um usuário."""
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="❌ Envie o ID numérico ou @username do usuário que terá o acesso revogado.", reply_markup=reply_markup)
    return GETTING_USER_ID_FOR_REVOKE

@admin_only
async def revoke_access_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o ID do usuário e pede confirmação para revogar."""
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)
    if not user_data:
        await update.message.reply_text("❌ Usuário não encontrado.")
        await show_main_admin_menu(update, context)
        return SELECTING_ACTION
    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)
    if not active_sub:
        await update.message.reply_text("Este usuário não possui uma assinatura ativa para revogar.")
        await show_main_admin_menu(update, context)
        return SELECTING_ACTION
    context.user_data['revoke_db_user_id'] = user_data['id']
    context.user_data['revoke_telegram_user_id'] = user_data['telegram_user_id']
    keyboard = [
        [InlineKeyboardButton("✅ SIM, REVOGAR AGORA", callback_data="revoke_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    await update.message.reply_text(
        f"⚠️ *ATENÇÃO* ⚠️\n\n"
        f"Você está prestes a revogar o acesso de *{user_data['first_name']}* (`{user_data['telegram_user_id']}`) "
        f"e removê-lo(a) de todos os grupos.\n\n"
        f"Esta ação é irreversível. Confirma?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    return CONFIRMING_REVOKE

@admin_only
async def revoke_access_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Executa a revogação do acesso."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Processando revogação...")
    db_user_id = context.user_data.get('revoke_db_user_id')
    telegram_user_id = context.user_data.get('revoke_telegram_user_id')
    admin_id = update.effective_user.id
    success = await db.revoke_subscription(db_user_id, f"revoked_by_admin_{admin_id}")
    if success:
        await db.create_log('admin_action', f"Admin {admin_id} revogou acesso do usuário {telegram_user_id}")
        removed_count = await scheduler.kick_user_from_all_groups(telegram_user_id, context.bot)
        await query.edit_message_text(f"✅ Acesso revogado com sucesso!\n\n👤 Usuário: {telegram_user_id}\n🚫 Removido de {removed_count} grupo(s).")
        try:
            await context.bot.send_message(telegram_user_id, "⚠️ Seu acesso foi revogado por um administrador.")
        except Exception as e:
            logger.error(f"Erro ao notificar usuário {telegram_user_id} sobre revogação: {e}")
    else:
        await query.edit_message_text("❌ Falha ao revogar o acesso no banco de dados.")
    context.user_data.clear()
    await show_main_admin_menu(update, context, is_edit=True)
    return SELECTING_ACTION

@admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de envio de mensagem global."""
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="📢 Envie a mensagem para o broadcast.\n\nUse /cancel para abortar.", reply_markup=reply_markup)
    return GETTING_BROADCAST_MESSAGE

@admin_only
async def broadcast_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe a mensagem para o broadcast e pede confirmação."""
    context.user_data['broadcast_message'] = update.message
    keyboard = [
        [InlineKeyboardButton("✅ SIM, ENVIAR AGORA", callback_data="broadcast_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    await update.message.reply_text("⚠️ A mensagem acima será enviada para TODOS os assinantes ativos.\n\nConfirma o envio?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRMING_BROADCAST

@admin_only
async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirma e inicia o processo de broadcast em segundo plano."""
    query = update.callback_query
    await query.answer()
    message_to_send = context.user_data.get('broadcast_message')
    if not message_to_send:
        await query.edit_message_text("❌ Erro: Mensagem não encontrada. Operação cancelada.")
        await show_main_admin_menu(update, context, is_edit=True)
        return SELECTING_ACTION
    await query.edit_message_text("📊 Buscando usuários... O envio começará em breve.")
    user_ids = await db.get_all_active_tg_user_ids()
    total_users = len(user_ids)
    if total_users == 0:
        await query.edit_message_text("Nenhum usuário ativo encontrado para o broadcast.")
        await show_main_admin_menu(update, context, is_edit=True)
        return SELECTING_ACTION
    await query.edit_message_text(f"📤 Iniciando envio para {total_users} usuários...\n\nVocê será notificado sobre o progresso.")
    await db.create_log('admin_action', f"Admin {update.effective_user.id} iniciou broadcast para {total_users} usuários")
    asyncio.create_task(run_broadcast(context, message_to_send, user_ids, query.message.chat_id, query.message.message_id))
    context.user_data.clear()
    return ConversationHandler.END

async def run_broadcast(context: ContextTypes.DEFAULT_TYPE, message_to_send, user_ids, admin_chat_id, admin_message_id):
    """Executa o envio do broadcast em si, com controle de rate limit e feedback de progresso."""
    sent, failed, blocked = 0, 0, 0
    total = len(user_ids)
    start_time = datetime.now()
    for i, user_id in enumerate(user_ids, 1):
        try:
            await context.bot.copy_message(chat_id=user_id, from_chat_id=message_to_send.chat_id, message_id=message_to_send.message_id)
            sent += 1
            await asyncio.sleep(0.1)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await context.bot.copy_message(chat_id=user_id, from_chat_id=message_to_send.chat_id, message_id=message_to_send.message_id)
                sent += 1
            except Exception:
                failed += 1
        except Forbidden: blocked += 1
        except BadRequest: failed += 1
        except Exception as e:
            logger.error(f"Erro inesperado no broadcast para {user_id}: {e}")
            failed += 1
        if i % 50 == 0 or i == total:
            try:
                elapsed = (datetime.now() - start_time).seconds
                remaining = ((elapsed / i) * (total - i)) if i > 0 else 0
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id, message_id=admin_message_id,
                    text=f"📊 Progresso: {i}/{total}\n✅ Enviados: {sent} | 🚫 Bloqueados: {blocked} | ❌ Falhas: {failed}\n⏱️ Restante: ~{int(remaining // 60)} min"
                )
            except BadRequest: pass
    elapsed_time = (datetime.now() - start_time).seconds
    await context.bot.edit_message_text(
        chat_id=admin_chat_id, message_id=admin_message_id,
        text=f"📢 *Broadcast Concluído!*\n\n✅ Enviados: {sent}\n🚫 Bloquearam: {blocked}\n❌ Falhas: {failed}\n⏱️ Duração: {elapsed_time // 60}m {elapsed_time % 60}s",
        parse_mode=ParseMode.MARKDOWN
    )
    await db.create_log('broadcast_complete', f"Broadcast concluído: {sent}/{total} enviados")

@admin_only
async def grant_new_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de envio de convites para um novo grupo."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📊 Buscando grupos cadastrados...")
    groups = await db.get_all_groups_with_names()
    if not groups:
        await query.edit_message_text("❌ Nenhum grupo encontrado. Use 'Gerenciar Grupos' para adicionar um.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]))
        return SELECTING_ACTION
    keyboard = [[InlineKeyboardButton(f"📁 {g.get('name', g['telegram_chat_id'])}", callback_data=f"new_group_select_{g['telegram_chat_id']}")] for g in groups]
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")])
    await query.edit_message_text("✉️ *Enviar Link de Novo Grupo*\n\nSelecione o grupo para enviar convites:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return SELECTING_NEW_GROUP

@admin_only
async def grant_new_group_select_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe a seleção do grupo e pede confirmação."""
    query = update.callback_query
    await query.answer()
    chat_id = int(query.data.split('_')[-1])
    context.user_data['new_group_chat_id'] = chat_id
    try:
        chat = await context.bot.get_chat(chat_id)
        group_name = chat.title
    except Exception as e:
        logger.error(f"Não foi possível obter informações do grupo {chat_id}: {e}")
        group_name = f"ID {chat_id}"
    keyboard = [
        [InlineKeyboardButton("✅ SIM, ENVIAR CONVITES", callback_data="new_group_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    await query.edit_message_text(f"⚠️ *CONFIRMAÇÃO* ⚠️\n\nVocê está prestes a enviar um convite para o grupo:\n📁 *{group_name}*\n\nSerá enviado para TODOS os assinantes ativos que ainda não são membros.\n\nDeseja continuar?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return CONFIRMING_NEW_GROUP_BROADCAST

@admin_only
async def grant_new_group_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirma e inicia o envio dos convites em segundo plano."""
    query = update.callback_query
    await query.answer()
    chat_id = context.user_data.get('new_group_chat_id')
    if not chat_id:
        await query.edit_message_text("❌ Erro: ID do grupo não encontrado.")
        return SELECTING_ACTION
    await query.edit_message_text("📊 Buscando usuários ativos... O envio começará em breve.")
    user_ids = await db.get_all_active_tg_user_ids()
    if not user_ids:
        await query.edit_message_text("❌ Nenhum usuário com assinatura ativa foi encontrado.")
        return SELECTING_ACTION
    await query.edit_message_text(f"📤 Iniciando envio de convites para {len(user_ids)} usuários...")
    await db.create_log('admin_action', f"Admin {update.effective_user.id} iniciou envio de links do grupo {chat_id}")
    asyncio.create_task(run_new_group_broadcast(context, chat_id, user_ids, query.message.chat_id, query.message.message_id))
    context.user_data.clear()
    return ConversationHandler.END

async def run_new_group_broadcast(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_ids: list[int], admin_chat_id: int, admin_message_id: int):
    """Executa o envio de convites em si, com verificação de membros e feedback de progresso."""
    sent, failed, already_in = 0, 0, 0
    total = len(user_ids)
    start_time = datetime.now()
    try:
        group_name = (await context.bot.get_chat(chat_id)).title
    except Exception:
        group_name = f"o grupo (ID: {chat_id})"
    for i, user_id in enumerate(user_ids, 1):
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator']:
                already_in += 1
                continue
            link = await context.bot.create_chat_invite_link(chat_id=chat_id, member_limit=1)
            await context.bot.send_message(chat_id=user_id, text=f"✨ Como nosso assinante, você ganhou acesso ao novo grupo:\n📁 *{group_name}*\n\nClique para entrar: {link.invite_link}", parse_mode=ParseMode.MARKDOWN)
            sent += 1
            await asyncio.sleep(0.5)
        except (BadRequest, Forbidden):
            failed += 1
        except Exception as e:
            logger.error(f"Erro ao processar usuário {user_id} para grupo {chat_id}: {e}")
            failed += 1
        if i % 30 == 0 or i == total:
            try:
                await context.bot.edit_message_text(chat_id=admin_chat_id, message_id=admin_message_id, text=f"📊 Progresso: {i}/{total}\n✅ Enviados: {sent} | 👤 Já membros: {already_in} | ❌ Falhas: {failed}")
            except BadRequest: pass
    elapsed = (datetime.now() - start_time).seconds
    await context.bot.edit_message_text(chat_id=admin_chat_id, message_id=admin_message_id, text=f"✉️ *Envio de Convites Concluído!*\n\n✅ Enviados: {sent}\n👤 Já eram membros: {already_in}\n❌ Falhas: {failed}\n⏱️ Duração: {elapsed//60}m {elapsed%60}s", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def manage_coupons_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra o painel de gerenciamento de cupons."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📊 Carregando cupons...")
    coupons = await db.get_all_coupons(include_inactive=True)
    text = f"🎟️ *Gerenciamento de Cupons*\n\nTotal de cupons: {len(coupons)}\n"
    if coupons:
        active_count = sum(1 for c in coupons if c.get('is_active'))
        text += f"✅ Ativos: {active_count}\n❌ Inativos: {len(coupons) - active_count}\n\n"
        text += "*Últimos cupons criados:*\n"
        for coupon in coupons[:10]:
            status = "✅" if coupon.get('is_active') else "❌"
            dtype_symbol = "%" if coupon.get('discount_type') == 'percentage' else "R$"
            value = coupon['discount_value']
            usage_limit = coupon.get('usage_limit') or '∞'
            text += f"{status} `{coupon['code']}` ({value}{dtype_symbol}) - Usos: {coupon.get('usage_count',0)}/{usage_limit}\n"
    keyboard = [
        [InlineKeyboardButton("➕ Criar Novo Cupom", callback_data="coupon_create")],
        [
            InlineKeyboardButton("🔴 Desativar", callback_data="coupon_deactivate"),
            InlineKeyboardButton("🟢 Reativar", callback_data="coupon_reactivate")
        ],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return MANAGING_COUPONS

@admin_only
async def create_coupon_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o processo de criação de um novo cupom."""
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_coupons")]]
    await query.edit_message_text(
        "🎟️ *Criar Novo Cupom*\n\n**Passo 1/4:** Envie o código do cupom (ex: PROMO10).\n\nUse apenas letras e números.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    return GETTING_COUPON_CODE

@admin_only
async def create_coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o código do novo cupom."""
    code = update.message.text.strip().upper()
    if not code.isalnum() or len(code) < 3:
        await update.message.reply_text("❌ Código inválido. Use apenas letras e números (mínimo 3 caracteres). Tente novamente.")
        return GETTING_COUPON_CODE
    existing = await db.get_coupon_by_code(code, include_inactive=True)
    if existing:
        await update.message.reply_text("❌ Este código já existe. Escolha outro.")
        return GETTING_COUPON_CODE
    context.user_data['coupon_code'] = code
    await update.message.reply_text(
        f"✅ Código: *{code}*\n\n**Passo 2/4:** Envie o valor do desconto.\n\n• Para porcentagem: `10%`\n• Para valor fixo: `R$5.00` ou `5`",
        parse_mode=ParseMode.MARKDOWN
    )
    return GETTING_COUPON_DISCOUNT

@admin_only
async def create_coupon_get_discount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o valor do desconto."""
    discount_text = update.message.text.strip().replace(',', '.')
    try:
        if '%' in discount_text:
            discount_type = 'percentage'
            discount_value = float(discount_text.replace('%', '').strip())
            if not 0 < discount_value <= 100: raise ValueError("Porcentagem deve ser entre 0 e 100.")
        else:
            discount_type = 'fixed'
            discount_value = float(discount_text.lower().replace('r$', '').strip())
            if discount_value <= 0: raise ValueError("Valor do desconto deve ser positivo.")
        context.user_data['coupon_discount_type'] = discount_type
        context.user_data['coupon_discount_value'] = discount_value
        await update.message.reply_text("✅ Desconto definido.\n\n**Passo 3/4:** O cupom terá data de expiração?\n\nDigite `SIM` ou `NAO`.", parse_mode=ParseMode.MARKDOWN)
        return GETTING_COUPON_VALIDITY
    except ValueError as e:
        await update.message.reply_text(f"❌ Valor inválido. Use formatos como `10%`, `R$5.00` ou `5`. Tente novamente.")
        return GETTING_COUPON_DISCOUNT

@admin_only
async def create_coupon_get_validity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Pergunta e define a data de validade."""
    response = update.message.text.strip().upper()
    if response == "SIM":
        context.user_data['coupon_needs_validity'] = True
        await update.message.reply_text("📅 Digite a data de expiração no formato `DD/MM/AAAA` (ex: `31/12/2024`).")
        return GETTING_COUPON_USAGE_LIMIT
    elif response in ["NAO", "NÃO"]:
        context.user_data['coupon_valid_until'] = None
        await update.message.reply_text("✅ Sem data de expiração.\n\n**Passo 4/4:** Qual o limite de usos?\n\nDigite um número ou `ILIMITADO`.", parse_mode=ParseMode.MARKDOWN)
        return GETTING_COUPON_USAGE_LIMIT
    else:
        await update.message.reply_text("❌ Resposta inválida. Digite `SIM` ou `NAO`.")
        return GETTING_COUPON_VALIDITY

@admin_only
async def create_coupon_get_usage_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe a data de validade (se aplicável) ou o limite de uso para finalizar a criação."""
    text_input = update.message.text.strip().upper()

    if context.user_data.get('coupon_needs_validity'):
        try:
            valid_until_naive = datetime.strptime(text_input, '%d/%m/%Y')
            valid_until = valid_until_naive.replace(hour=23, minute=59, second=59, tzinfo=TIMEZONE_BR)
            if valid_until < datetime.now(TIMEZONE_BR):
                await update.message.reply_text("❌ A data de expiração deve ser no futuro. Tente novamente.")
                return GETTING_COUPON_USAGE_LIMIT

            context.user_data['coupon_valid_until'] = valid_until
            context.user_data.pop('coupon_needs_validity')
            await update.message.reply_text(f"✅ Data definida para {text_input}.\n\n**Passo 4/4:** Qual o limite de usos?\n\nDigite um número ou `ILIMITADO`.", parse_mode=ParseMode.MARKDOWN)
            return GETTING_COUPON_USAGE_LIMIT
        except ValueError:
            await update.message.reply_text("❌ Data inválida. Use o formato `DD/MM/AAAA`. Tente novamente.")
            return GETTING_COUPON_USAGE_LIMIT
    else:
        usage_limit = None
        if text_input != "ILIMITADO":
            try:
                usage_limit = int(text_input)
                if usage_limit <= 0: raise ValueError()
            except ValueError:
                await update.message.reply_text("❌ Valor inválido. Digite um número positivo ou `ILIMITADO`.")
                return GETTING_COUPON_USAGE_LIMIT

        coupon_data = {
            "code": context.user_data.get('coupon_code'),
            "discount_type": context.user_data.get('coupon_discount_type'),
            "discount_value": context.user_data.get('coupon_discount_value'),
            "valid_until": context.user_data.get('coupon_valid_until'),
            "usage_limit": usage_limit
        }
        coupon_data['valid_from'] = None
        coupon = await db.create_coupon(**coupon_data)

        if coupon:
            await update.message.reply_text("✅ *Cupom criado com sucesso!*", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("❌ Erro ao criar cupom no banco de dados.")

        context.user_data.clear()
        await show_main_admin_menu(update, context)
        return SELECTING_ACTION

@admin_only
async def deactivate_coupon_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o processo de desativação de cupom."""
    query = update.callback_query
    await query.answer()
    coupons = await db.get_all_coupons(include_inactive=False)
    if not coupons:
        await query.edit_message_text("✅ Não há cupons ativos para desativar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_coupons")]]))
        return MANAGING_COUPONS
    text = "🔴 *Desativar Cupom*\n\nDigite o código do cupom que deseja desativar:\n\n"
    for coupon in coupons[:15]: text += f"• `{coupon.get('code')}`\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_coupons")]]), parse_mode=ParseMode.MARKDOWN)
    return GETTING_COUPON_TO_DEACTIVATE

@admin_only
async def deactivate_coupon_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o código e executa a desativação."""
    code = update.message.text.strip().upper()
    success = await db.deactivate_coupon(code)
    if success:
        await update.message.reply_text(f"✅ Cupom `{code}` foi desativado com sucesso!", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ Erro ao desativar o cupom `{code}`. Verifique se o código existe e está ativo.", parse_mode=ParseMode.MARKDOWN)
    await show_main_admin_menu(update, context)
    return SELECTING_ACTION

@admin_only
async def reactivate_coupon_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia processo de reativação de cupom."""
    query = update.callback_query
    await query.answer()
    all_coupons = await db.get_all_coupons(include_inactive=True)
    inactive_coupons = [c for c in all_coupons if not c.get('is_active')]
    if not inactive_coupons:
        await query.edit_message_text("✅ Não há cupons inativos para reativar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_coupons")]]))
        return MANAGING_COUPONS
    text = "🟢 *Reativar Cupom*\n\nDigite o código do cupom que deseja reativar:\n\n"
    for coupon in inactive_coupons[:15]: text += f"• `{coupon.get('code')}`\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_manage_coupons")]]), parse_mode=ParseMode.MARKDOWN)
    return GETTING_COUPON_TO_REACTIVATE

@admin_only
async def reactivate_coupon_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o código e executa a reativação."""
    code = update.message.text.strip().upper()
    success = await db.reactivate_coupon(code)
    if success:
        await update.message.reply_text(f"✅ Cupom `{code}` foi reativado com sucesso!", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ Erro ao reativar o cupom `{code}`. Verifique se o código existe e está inativo.", parse_mode=ParseMode.MARKDOWN)
    await show_main_admin_menu(update, context)
    return SELECTING_ACTION


# --- SEÇÃO 6: CANCELAMENTO E DEFINIÇÃO DA CONVERSATION HANDLER ---

@admin_only
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a operação atual, limpa dados e volta ao menu principal."""
    text = "❌ Operação cancelada."
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text=text)
        except BadRequest: pass
    elif update.message:
        await update.message.reply_text(text)
    await asyncio.sleep(1)
    await show_main_admin_menu(update, context, is_edit=update.callback_query is not None)
    return SELECTING_ACTION

def get_admin_conversation_handler() -> ConversationHandler:
    """Retorna o ConversationHandler completo com todos os fluxos administrativos."""
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_panel)],
        name="admin-conversation",
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(view_stats, pattern="^admin_stats$"),
                CallbackQueryHandler(manage_referrals_start, pattern="^admin_referrals$"),
                CallbackQueryHandler(check_user_start, pattern="^admin_check_user$"),
                CallbackQueryHandler(search_transactions_start, pattern="^admin_transactions$"),
                CallbackQueryHandler(grant_access_start, pattern="^admin_grant_access$"),
                CallbackQueryHandler(revoke_access_start, pattern="^admin_revoke_access$"),
                CallbackQueryHandler(broadcast_start, pattern="^admin_broadcast$"),
                CallbackQueryHandler(manage_coupons_start, pattern="^admin_manage_coupons$"),
                CallbackQueryHandler(manage_groups_start, pattern="^admin_manage_groups$"),
                CallbackQueryHandler(view_logs, pattern="^admin_view_logs$"),
                CallbackQueryHandler(grant_new_group_start, pattern="^admin_grant_new_group$"),
                CallbackQueryHandler(cancel, pattern="^admin_cancel$"),
            ],
            MANAGING_REFERRALS: [CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")],
            GETTING_USER_ID_FOR_CHECK: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_user_receive_id)],
            GETTING_TRANSACTION_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_transactions_execute)],
            GETTING_USER_ID_FOR_GRANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_access_receive_id)],
            SELECTING_PLAN_FOR_GRANT: [CallbackQueryHandler(grant_access_select_plan, pattern="^grant_plan_")],
            GETTING_USER_ID_FOR_REVOKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, revoke_access_receive_id)],
            CONFIRMING_REVOKE: [CallbackQueryHandler(revoke_access_confirm, pattern="^revoke_confirm$")],
            GETTING_BROADCAST_MESSAGE: [MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND, broadcast_receive_message)],
            CONFIRMING_BROADCAST: [CallbackQueryHandler(broadcast_confirm, pattern="^broadcast_confirm$")],
            FILTERING_LOGS: [
            CallbackQueryHandler(view_logs, pattern="^logs_view$"),
            CallbackQueryHandler(logs_filter_type_menu, pattern="^logs_filter_type_menu$"),
            CallbackQueryHandler(logs_filter_date_menu, pattern="^logs_filter_date_menu$"),
            CallbackQueryHandler(logs_set_filter, pattern="^logs_set_"),
            CallbackQueryHandler(logs_clear_filters, pattern="^logs_filter_clear$"),
            CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
            ],
            MANAGING_GROUPS: [
                CallbackQueryHandler(add_group_start, pattern="^group_add$"),
                CallbackQueryHandler(remove_group_start, pattern="^group_remove$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
            ],
            GETTING_GROUP_FORWARD: [
                MessageHandler(filters.FORWARDED & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL), add_group_receive_forward),
                CallbackQueryHandler(back_to_manage_groups, pattern="^admin_manage_groups_back$"),
            ],
            CONFIRMING_GROUP_ADD: [
                CallbackQueryHandler(add_group_confirm, pattern="^add_group_confirm$"),
                CallbackQueryHandler(back_to_manage_groups, pattern="^admin_manage_groups_back$"),
            ],
            GETTING_GROUP_TO_REMOVE: [
                CallbackQueryHandler(remove_group_confirm_choice, pattern="^remove_group_"),
                CallbackQueryHandler(back_to_manage_groups, pattern="^admin_manage_groups_back$"),
            ],
            CONFIRMING_GROUP_REMOVE: [
                CallbackQueryHandler(remove_group_execute, pattern="^remove_group_confirmed$"),
                CallbackQueryHandler(back_to_manage_groups, pattern="^admin_manage_groups_back$"),
            ],
            MANAGING_COUPONS: [
                CallbackQueryHandler(create_coupon_start, pattern="^coupon_create$"),
                CallbackQueryHandler(deactivate_coupon_start, pattern="^coupon_deactivate$"),
                CallbackQueryHandler(reactivate_coupon_start, pattern="^coupon_reactivate$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
            ],
            GETTING_COUPON_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_coupon_get_code),
                CallbackQueryHandler(back_to_manage_coupons, pattern="^admin_manage_coupons$"),
            ],
            GETTING_COUPON_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_coupon_get_discount)],
            GETTING_COUPON_VALIDITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_coupon_get_validity)],
            GETTING_COUPON_USAGE_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_coupon_get_usage_limit)],
            GETTING_COUPON_TO_DEACTIVATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, deactivate_coupon_execute),
                CallbackQueryHandler(back_to_manage_coupons, pattern="^admin_manage_coupons$"),
            ],
            GETTING_COUPON_TO_REACTIVATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reactivate_coupon_execute),
                CallbackQueryHandler(back_to_manage_coupons, pattern="^admin_manage_coupons$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
            CallbackQueryHandler(back_to_manage_groups, pattern="^admin_manage_groups_back$"),
            CallbackQueryHandler(back_to_manage_coupons, pattern="^admin_manage_coupons$"),
            CommandHandler("admin", admin_panel),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True
    )
